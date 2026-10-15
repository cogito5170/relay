"""Codex 이미지 생성 파이프라인 (`relay image`).

    Claude (의미 스펙 image-spec.json) → Codex (상세 프롬프트 작성 + 이미지 생성) → task 폴더 (image.png)

Claude 토큰을 아끼는 장치
- Claude 는 무엇을 그릴지만 **구조화된 짧은 스펙**으로 쓴다 (subject · style · mood · composition …).
  수백 단어짜리 이미지 프롬프트를 쓰고 다듬는 일은 Codex(ChatGPT 구독 쿼터)가 한다.
- Codex 는 ChatGPT OAuth 로그인 상태의 내장 이미지 생성 도구를 쓴다. API 키가 필요 없다.
- 결과(image.png · image-prompt.md · image.json)는 task 폴더에 남아 git 으로 Claude 세션에 돌아온다.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from relay.pipeline import RunnerMissing, _fill, installed_runners, quota_exhausted

SPEC_FILE = "image-spec.json"
PROMPT_FILE = "image-prompt.md"
REPORT_FILE = "image.json"
IMAGE_STEM = "image"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")

# 스펙은 "의미" 만 담는다. 렌더링 세부(렌즈·조명 수치·프롬프트 문구)는 Codex 의 몫이다.
SPEC_FIELDS = {
    "subject": "무엇을 그리는가 (필수)",
    "purpose": "어디에 쓰는가 (블로그 표지, 앱 아이콘 …)",
    "style": "화풍·매체 (flat illustration, photo, watercolor …)",
    "mood": "분위기·감정",
    "composition": "구도·시점·배치",
    "palette": "색감",
    "lighting": "빛",
    "text": "그림 안에 들어갈 글자 (없으면 비움)",
    "size": "1024x1024 · 1536x1024 · 1024x1536 중 하나",
    "avoid": "넣지 말 것 (문자열 또는 목록)",
    "notes": "기타 제약",
}
SIZES = ("1024x1024", "1536x1024", "1024x1536")
MAX_FIELD = 600

CODEX_PROMPT = """You are the image director for this request. Another agent wrote only a semantic spec;
turning it into a detailed image prompt is your job.

## Semantic spec (JSON)
{spec}

## Do exactly this
1. Expand the spec into one detailed English image-generation prompt: subject details, setting,
   style and medium, composition and camera/viewpoint, lighting, color palette, mood, and the
   exclusions from "avoid". Honor every field; invent only details that serve the spec.
   Write it to `{prompt_file}` in the current directory as:
   `# Prompt` (the prompt itself), then `# Notes` (one line per spec field: how you interpreted it).
2. Generate the image from that prompt with your built-in image generation tool at size {size}.
3. Save the generated image in the current directory as `{image_stem}.png`
   (copy it from wherever the tool stored it). Generate exactly one image.
4. Do not create, modify or delete any other file. Do not use the network except through the
   image tool. If you have no image generation tool, still write `{prompt_file}`, then print
   `IMAGE_TOOL_UNAVAILABLE` and stop.
5. Also print the final prompt to stdout between a line `{begin}` and a line `{end}`
   (the file write can fail in some sandboxes; the caller saves it from your output).
6. Finish with one line: `SAVED {image_stem}.png` or `IMAGE_TOOL_UNAVAILABLE`.
"""
PROMPT_BEGIN = "=====PROMPT====="
PROMPT_END = "=====END PROMPT====="


def load_spec(path: Path) -> dict:
    return validate_spec(json.loads(path.read_text(encoding="utf-8")))


def validate_spec(spec: dict) -> dict:
    """알 수 없는 키·과도한 길이를 막는다. 스펙은 Codex 프롬프트에 그대로 들어간다."""
    if not isinstance(spec, dict):
        raise ValueError("image spec 은 JSON 객체여야 합니다")
    unknown = sorted(set(spec) - set(SPEC_FIELDS))
    if unknown:
        raise ValueError(f"알 수 없는 스펙 필드: {', '.join(unknown)} (허용: {', '.join(SPEC_FIELDS)})")
    if not str(spec.get("subject", "")).strip():
        raise ValueError("subject 는 필수입니다")
    clean = {}
    for key, value in spec.items():
        if isinstance(value, list):
            value = [str(v).strip() for v in value if str(v).strip()]
            size = sum(len(v) for v in value)
        else:
            value = str(value).strip()
            size = len(value)
        if size > MAX_FIELD:
            raise ValueError(f"{key} 는 {MAX_FIELD}자 이하여야 합니다")
        if value:
            clean[key] = value
    clean.setdefault("size", SIZES[0])
    if clean["size"] not in SIZES:
        raise ValueError(f"size 는 {', '.join(SIZES)} 중 하나여야 합니다")
    return clean


def build_prompt(spec: dict) -> str:
    return CODEX_PROMPT.format(spec=json.dumps(spec, ensure_ascii=False, indent=2),
                               prompt_file=PROMPT_FILE, image_stem=IMAGE_STEM, size=spec["size"],
                               begin=PROMPT_BEGIN, end=PROMPT_END)


def prompt_from_output(out: str) -> str:
    """출력에서 마지막 PROMPT 블록을 꺼낸다. 마커는 한 줄 전체일 때만 인정한다 (프롬프트 되풀이 무시)."""
    lines = [line.rstrip() for line in out.splitlines()]
    ends = [i for i, line in enumerate(lines) if line.strip() == PROMPT_END]
    for end in reversed(ends):
        starts = [i for i in range(end) if lines[i].strip() == PROMPT_BEGIN]
        if starts:
            return "\n".join(lines[starts[-1] + 1:end]).strip()
    return ""


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def _find_image(task: Path) -> Path | None:
    for ext in IMAGE_EXTS:
        path = task / f"{IMAGE_STEM}{ext}"
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _harvest(since: float, search: Path) -> Path | None:
    """Codex 가 이미지를 task 폴더에 복사하지 못했을 때, CODEX_HOME 에 새로 생긴 이미지를 찾는다."""
    if not search.is_dir():
        return None
    fresh = [p for p in search.rglob("*") if p.suffix.lower() in IMAGE_EXTS and p.is_file()
             and p.stat().st_mtime >= since]
    return max(fresh, key=lambda p: p.stat().st_mtime) if fresh else None


def generate(task: Path, conf: dict, log=print) -> tuple[int, dict]:
    """task/image-spec.json → Codex → task/image.png. (rc, report) 를 돌려주고 image.json 에 기록한다."""
    spec = load_spec(task / SPEC_FILE)
    prompt = build_prompt(spec)
    timeout = conf["image"]["timeout"]
    report = {"task": task.name, "spec": spec, "status": "failed", "runner": "", "attempts": [],
              "image": "", "prompt_file": "", "source": ""}
    outputs = []
    started_wall = time.time() - 1
    started = time.monotonic()
    try:
        candidates = installed_runners(conf["runners"]["image"])
    except RunnerMissing as e:
        report["error"] = str(e)
        candidates = []

    for template in candidates:
        argv = _fill(template, {"prompt": prompt})
        log(f"  codex 실행: {' '.join(a for a in template if a != '{prompt}')} …")
        try:
            proc = subprocess.run(argv, cwd=task, capture_output=True, text=True, timeout=timeout,
                                  stdin=subprocess.DEVNULL)
            rc, out = proc.returncode, proc.stdout + proc.stderr
        except subprocess.TimeoutExpired:
            rc, out = 124, f"timeout={timeout}"
        outputs.append(out)
        report["attempts"].append({"argv": [a for a in template if a != "{prompt}"], "exit_code": rc,
                                   "tail": out.strip()[-800:]})
        report["runner"] = template[0]
        image = _find_image(task)
        if not image:
            found = _harvest(started_wall, codex_home())
            if found:
                image = task / f"{IMAGE_STEM}{found.suffix.lower()}"
                shutil.copyfile(found, image)
                report["source"] = str(found)
        if image:
            report.update(status="ok", image=image.name)
            break
        if quota_exhausted(out):
            # 다음 후보도 같은 ChatGPT 계정이라 다시 시도해도 같은 결과다
            limit = next((l.strip() for l in out.splitlines() if quota_exhausted(l)), "")
            report["error"] = f"Codex 사용 한도 소진: {limit}"
            break
        # codex 는 프롬프트를 출력에 되풀이하므로, 마커가 한 줄 전체일 때만 인정한다
        if any(line.strip() == "IMAGE_TOOL_UNAVAILABLE" for line in out.splitlines()):
            report["error"] = "Codex 에 이미지 생성 도구가 없습니다 (ChatGPT 로그인·codex 버전 확인)"
            break

    if not (task / PROMPT_FILE).is_file():
        # Codex 샌드박스가 파일 쓰기를 막아도 (예: bubblewrap 실패) 출력에 남긴 프롬프트는 살린다
        text = next(filter(None, (prompt_from_output(o) for o in reversed(outputs))), "")
        if text:
            (task / PROMPT_FILE).write_text(f"# Prompt\n{text}\n", encoding="utf-8")
    if (task / PROMPT_FILE).is_file():
        report["prompt_file"] = PROMPT_FILE
    if report["status"] != "ok":
        report.setdefault("error", "이미지가 생성되지 않았습니다 (attempts 의 tail 확인)")
    report["seconds"] = round(time.monotonic() - started, 3)
    (task / REPORT_FILE).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return (0 if report["status"] == "ok" else 2), report
