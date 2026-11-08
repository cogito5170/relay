# relay 설계 근거 논문 10편

작성일 2026-11-08. 원문 PDF는 `papers/`에 내려받았고(전부 첫 4바이트 `%PDF`, 첫 쪽 제목·저자 확인) `pdftotext`로만 읽었다. 서지(저자·제목·연도)는 첫 쪽에서 옮겼다. 인용 구절은 PDF 텍스트를 그대로 복사했다(줄바꿈만 공백으로 합침). 쪽 번호는 PDF 파일의 쪽 순서다(논문 인쇄 쪽 번호와 다를 수 있다).

용어 약속: 전문 용어는 처음 나올 때 한국어 풀이와 괄호 원어를 함께 적는다. 맥락 창(context window)은 모델이 한 번에 읽을 수 있는 입력 길이 한도, 에이전트(agent)는 도구를 써서 스스로 여러 단계를 수행하는 모델 기반 프로그램이다.

## 요약 표

| id | 논문 | 학회·연도 | relay 설계 요소 | 함의 한 줄 |
|---|---|---|---|---|
| P1 | MemGPT (Packer 외) | arXiv 2023 (v2 2024) | 대화 자동 보관·색인 재검색(archive/recall), 수동 압축 | 요약만 보는 쪽보다 전체 기록을 쪽 단위로 다시 찾는 쪽이 낫다는 근거. 보관은 지지, "요약만 남기기"는 보완 필요 |
| P2 | Generative Agents (Park 외) | UIST 2023 | archive/recall 색인, 반성 | 기억을 최신성·중요도·관련성으로 찾아야 하고 가장 흔한 오류는 "관련 기억을 못 찾음" |
| P3 | Reflexion (Shinn 외) | arXiv 2023 (v4) | state.md, 실행 가능한 완료 조건 | 짧은 글 기억 1~3개로 충분히 개선됨. 단 모델이 만든 테스트는 틀릴 수 있음 |
| P4 | Lost in the Middle (Liu 외) | arXiv 2023 (v3) | 짧은 고정 컨텍스트, 세션 분할, state.md 배치 | 긴 입력의 중간 정보는 잘 안 쓰인다. 세션 분할을 지지, state.md 핵심은 앞·뒤에 두라는 보완 |
| P5 | CoALA (Sumers 외) | TMLR 2024 | 상태·보관·지침의 층 분리 | 작업 기억/사건 기억/절차 기억 구분이 relay 구성과 대응. 절차 기억 쓰기는 더 위험 |
| P6 | Voyager (Wang 외) | arXiv 2023 (v2) | 필요할 때만 불러오는 지침, 완료 판정 | 기술 도서관은 지지. 그러나 모델의 자체 검증이 가장 중요했다는 결과는 "모델 판단 금지"에 부분 반박 |
| P7 | ACE (Zhang 외) | ICLR 2026 | state.md 180~250단어, 매 세션 다시 쓰기 | 간결 편향·맥락 붕괴: 요약을 통째로 다시 쓰면 세부가 닳는다. 설계 변경 시사 |
| P8 | LongMemEval (Wu 외) | ICLR 2025 | archive/recall 색인 설계 | 요약·사실 추출 압축은 정보를 잃는다. 색인 단위와 시간 정보가 중요 |
| P9 | SWE-agent (Yang 외) | arXiv 2024 (v3) | 검사기(checker), 간결한 결과 | 에이전트용 인터페이스와 편집 시 검사 장치가 성능을 크게 올림 |
| P10 | Measuring AI Ability to Complete Long Software Tasks (Kwa 외, METR) | arXiv 2025 (v4) | 작업 카드 분할, 모델별 분담 | 긴 작업일수록 성공률이 급락. 분할·기계적 완료 조건을 지지 |

## P1. MemGPT: Towards LLMs as Operating Systems

- 서지: Charles Packer, Sarah Wooders, Kevin Lin, Vivian Fang, Shishir G. Patil, Ion Stoica, Joseph E. Gonzalez. arXiv:2310.08560v2, 2024-02-12. https://arxiv.org/abs/2310.08560 (파일 `papers/memgpt.pdf`, 13쪽)
- 게재처: 첫 쪽에 학회 표기 없음. arXiv로 기재한다.

핵심 주장
1. 맥락 창이 한정되어 있으므로, 운영체제의 쪽 교체(paging)에서 따온 가상 컨텍스트 관리(virtual context management)를 쓴다.
   - 원문(p1): "we propose virtual context management, a technique drawing inspiration from hierarchical memory systems in traditional operating systems which provide the illusion of an extended virtual memory via paging between physical memory and disk."
   - 설명: 모델이 읽는 작은 주 컨텍스트와, 바깥의 보관소(archival)·재호출 저장소(recall)를 나눠 필요한 것만 끌어온다. 이는 relay의 state.md(작은 상태)와 archive/recall(원문 보관·색인)의 구조와 같다.
2. 대화 원문은 재호출 저장소에 전부 쓰고, 컨텍스트가 차오르면 경고를 줘서 중요한 것을 옮겨 적게 한다.
   - 원문(p3): "the queue manager inserts a system message into the queue warning the LLM of an impending queue eviction (a ‘memory pressure‘ warning) to allow the LLM to use MemGPT functions to store important information contained in the FIFO queue to working context or archival storage"
   - 설명: 압축 직전에 중요한 것을 저장하게 하는 장치. relay의 "세션 끊기 전 state.md에 결정·다음 단계 기록"과 같은 역할이지만 MemGPT는 모델이 스스로 하고 relay는 수동이다.
3. 요약만 보는 기준선은 전체 기록을 쪽 단위로 검색하는 방식에 진다.
   - 원문(p5): "The baselines are able to see a lossy summarization of the past five conversations to mimic an extended recursive summarization procedure, while MemGPT instead has access to the full conversation history but must access it via paginated search queries to recall memory"
   - 설명: 같은 문단에서 MemGPT가 기준선보다 정확도·ROUGE가 높다고 보고한다. 손실 있는 요약(lossy summarization)만 믿는 구조의 약점을 보인다.

relay 설계에 주는 함의: archive/recall(전체 보관 + 필요 줄만 재검색)을 직접 지지한다. state.md 180~250단어는 요약일 뿐이므로 원문 보관이 반드시 함께 있어야 한다는 보완 근거다. 차이점: MemGPT는 모델이 기억 관리를 자율 수행하지만 relay는 수동 압축이고, 이 논문이 둘의 우열을 비교하지는 않았다.

## P2. Generative Agents: Interactive Simulacra of Human Behavior

- 서지: Joon Sung Park, Joseph C. O’Brien, Carrie J. Cai, Meredith Ringel Morris, Percy Liang, Michael S. Bernstein. UIST ’23, October 29–November 1, 2023, San Francisco. arXiv:2304.03442v2. https://arxiv.org/abs/2304.03442 (파일 `papers/genagents.pdf`, 22쪽)

핵심 주장
1. 경험 전체를 자연어로 기록하는 기억 흐름(memory stream)을 두고, 필요한 것을 최신성·중요도·관련성으로 골라 꺼낸다.
   - 원문(p2): "The first is the memory stream, a long-term memory module that records, in natural language, a comprehensive list of the agent’s experiences. A memory retrieval model combines relevance, recency, and importance to surface the records needed to inform the agent’s moment-to-moment behavior."
   - 설명: archive/recall에서 색인으로 필요한 줄을 찾을 때 단순 키워드가 아니라 여러 신호를 섞는 근거.
2. 기억·반성(reflection, 기억을 상위 추론으로 종합)·계획 각각을 빼면 성능이 떨어진다.
   - 원문(p3): "We compared several ablations that limit agents’ access to memory, reflection, and planning. We observe that each of these components is critical to strong performance across these interview tasks."
3. 가장 흔한 실패는 기억 검색 실패다.
   - 원문(p3): "the most common errors arose when the agent failed to retrieve relevant memories, fabricated embellishments to the agent’s memory"
   - 설명: 보관만 해서는 부족하고 검색 품질이 병목이다. 기억을 지어내는 오류도 있으므로 재호출 결과는 원문 줄 그대로 돌려줘야 한다.

relay 설계에 주는 함의: archive/recall을 지지하고, 색인이 최신성·중요도를 반영하도록 보완하라는 시사. 반성은 relay의 "결정·배운 점" 항목과 대응한다. 한계: 시뮬레이션 마을 환경이라 코딩 작업과는 거리가 있다.

## P3. Reflexion: Language Agents with Verbal Reinforcement Learning

- 서지: Noah Shinn, Federico Cassano, Edward Berman, Ashwin Gopinath, Karthik Narasimhan, Shunyu Yao. arXiv:2303.11366v4, 2023-10-10 (첫 쪽에 학회 표기 없음. NeurIPS 2023 게재는 일반 지식이며 확인 안 함). https://arxiv.org/abs/2303.11366 (파일 `papers/reflexion.pdf`, 19쪽)

핵심 주장
1. 가중치를 갱신하지 않고, 실패에 대한 글 반성을 에피소드 기억 완충(episodic memory buffer)에 남겨 다음 시도에 넣는다.
   - 원문(p1): "Reflexion agents verbally reflect on task feedback signals, then maintain their own reflective text in an episodic memory buffer to induce better decision-making in subsequent trials."
   - 설명: 세션이 끝날 때 짧은 글로 교훈을 남기고 다음 세션이 읽는 relay의 구조와 같다.
2. 기억은 1~3개로 작게 제한한다.
   - 원문(p5): "we bound mem by a maximum number of stored experiences, Ω (usually set to 1-3) to adhere to max context LLM limitations."
   - 설명: 작은 고정 기억이 실제로 쓸 만하다는 증거(state.md의 짧은 분량을 지지). 저자도 한계로 인정한다(p9: "we limit long-term memory to a sliding window with maximum capacity").
3. 실행으로 판정하는 신호가 중요하나, 모델이 만든 테스트는 틀릴 수 있다.
   - 원문(p8): "the false positive test execution rate for MBPP Python is 16.3% while the rate for HumanEval Python is a mere 1.4%"
   - 설명: 자체 생성 테스트의 거짓 통과(false positive)가 어떤 데이터에서는 16.3%였다. 또 p8 표에서 테스트 생성만 빼고 반성만 남기면(0.52) 기준 모델(0.60)보다 나빠진다.

relay 설계에 주는 함의: 짧은 state.md와 "배운 점" 기록을 지지하고, 셸 명령 완료 조건(done_when)을 지지한다. 보완: 완료 조건 자체가 부실하면(거짓 통과) 종료 코드 0도 믿을 수 없으므로 done_when의 품질 점검이 필요하다.

## P4. Lost in the Middle: How Language Models Use Long Contexts

- 서지: Nelson F. Liu, Kevin Lin, John Hewitt, Ashwin Paranjape, Michele Bevilacqua, Fabio Petroni, Percy Liang. arXiv:2307.03172v3, 2023-11-20. 게재처 TACL 2024는 일반 지식이며 PDF 첫 쪽에는 없음(확인 안 함). https://arxiv.org/abs/2307.03172 (파일 `papers/litm.pdf`, 18쪽)

핵심 주장
1. 관련 정보 위치가 U자형 성능 곡선을 만든다.
   - 원문(p2): "language model performance is highest when relevant information occurs at the very beginning (primacy bias) or end of its input context (recency bias), and performance significantly degrades when models must access and use information in the middle of their input context."
   - 설명: 처음과 끝은 잘 쓰고 중간은 잘 못 쓴다.
2. 맥락 창을 늘린 모델이라고 더 잘 쓰지는 않는다.
   - 원문(p5): "Extended-context models are not necessarily better at using input context."
3. 문서를 더 넣어도 이득이 거의 없고 비용만 커진다.
   - 원문(p10): "Using more than 20 retrieved documents only marginally improves reader performance (∼1.5% for GPT-3.5Turbo and ∼1% for Claude-1.3), while significantly increasing the input context length (and thus latency and cost)."

relay 설계에 주는 함의: 긴 대화를 끌고 가지 말고 세션을 끊는 설계, 고정 컨텍스트를 작게 유지하는 설계, 필요한 줄만 재검색하는 설계를 모두 지지한다. 구체 보완: state.md 안에서도 가장 중요한 항목(다음 단계, 대기 질문)을 맨 앞이나 맨 끝에 두라. 한계: 2023년 모델(GPT-3.5, Claude-1.3 등) 결과라 최신 모델에서도 같은지는 이 논문으로 확인할 수 없다.

## P5. Cognitive Architectures for Language Agents (CoALA)

- 서지: Theodore R. Sumers, Shunyu Yao, Karthik Narasimhan, Thomas L. Griffiths. Transactions on Machine Learning Research (02/2024). arXiv:2309.02427v3. https://arxiv.org/abs/2309.02427 (파일 `papers/coala.pdf`, 32쪽)

핵심 주장
1. 언어 에이전트의 기억을 단기 작업 기억(working memory)과 장기 기억 셋(사건·의미·절차)으로 나눈다.
   - 원문(p9): "These include short-term working memory and several long-term memories: episodic, semantic, and procedural."
   - 설명: relay 대응. state.md = 작업 기억을 파일로 이월한 것, archive = 사건 기억(episodic memory), 전역 지시·지침 = 절차 기억(procedural memory).
2. 행동을 외부 접지(grounding), 추론, 검색, 학습으로 나눠 기억 읽기·쓰기를 명시한다.
   - 원문(p8): "internal actions can be further decomposed into three kinds: retrieval (read from long-term memory; Section 4.3), reasoning (update the short-term working memory with LLM; Section 4.4), and learning (write to long-term memory; Section 4.5)."
3. 절차 기억(지침·코드)을 에이전트가 직접 고쳐 쓰는 것은 위험하다.
   - 원문(p10): "it is significantly riskier than writing to episodic or semantic memory, as it can easily introduce bugs or allow an agent to subvert its designers’ intentions"

relay 설계에 주는 함의: 상태·보관·지침을 층으로 나눈 설계를 개념적으로 지지한다. 특히 모델이 전역 지시(CLAUDE.md 등)나 상세 지침을 자동으로 고치게 하지 말고 사람 승인을 거치게 하라는 근거. 이 논문은 분류 틀을 제시하는 개념 논문이고 성능 실험은 없다.

## P6. VOYAGER: An Open-Ended Embodied Agent with Large Language Models

- 서지: Guanzhi Wang, Yuqi Xie, Yunfan Jiang, Ajay Mandlekar, Chaowei Xiao, Yuke Zhu, Linxi “Jim” Fan, Anima Anandkumar. arXiv:2305.16291v2, 2023-10-19. 게재처 표기는 첫 쪽에 없음. https://arxiv.org/abs/2305.16291 (파일 `papers/voyager.pdf`, 42쪽)

핵심 주장
1. 실행 가능한 코드로 된 기술 도서관(skill library)을 계속 늘리고, 필요할 때 검색해 쓴다.
   - 원문(p1): "an ever-growing skill library of executable code for storing and retrieving complex behaviors"
   - 설명: 상세 지침을 미리 다 올리지 않고 필요할 때 불러오는 relay의 지침 설계와 유사하다.
2. 과제 완료를 확인한 뒤에야 도서관에 올린다.
   - 원문(p3): "repeats the process until a self-verification module confirms the task completion, at which point we commit the program to the skill library"
   - 설명: 확인 후 저장이라는 점은 relay의 done_when과 같다. 다만 확인 주체가 환경 피드백을 보는 모델(GPT-4)이다.
3. 자체 검증을 빼면 성능이 가장 크게 떨어진다.
   - 원문(p9): "Self-verification is the most important among all the feedback types. Removing the module leads to a significant drop (−73%) in the discovered item count."

relay 설계에 주는 함의: 필요 시 불러오는 지침을 지지한다. 반박 가능성: 모델의 자체 검증이 가장 중요한 요소였다는 결과는 "모델 판단으로 완료를 쓰지 않는다"는 원칙과 긴장 관계다. 단, Minecraft에는 셸 종료 코드 같은 기계 판정이 없고, 이 논문은 모델 검증과 기계 판정을 비교하지 않았다. 따라서 "기계 판정이 가능한 곳은 done_when, 불가능한 곳은 별도 검증 모델 단계(결과는 파일로)"로 보완할 수 있다.

## P7. Agentic Context Engineering (ACE): Evolving Contexts for Self-Improving Language Models

- 서지: Qizheng Zhang, Changran Hu, Shubhangi Upasani, Boyuan Ma, Fenglu Hong, Vamsidhar Kamanuru, Jay Rainton, Chen Wu, Mengmeng Ji, Hanchen Li, Urmish Thakker, James Zou, Kunle Olukotun. Published as a conference paper at ICLR 2026. arXiv:2510.04618v3, 2026-03-29. https://arxiv.org/abs/2510.04618 (파일 `papers/ace.pdf`, 32쪽)

핵심 주장
1. 컨텍스트를 요약하면 세부가 빠지는 간결 편향(brevity bias)과, 반복해서 다시 쓰면 세부가 닳는 맥락 붕괴(context collapse)가 생긴다.
   - 원문(p1): "Prior approaches improve usability but often suffer from brevity bias, which drops domain insights for concise summaries, and from context collapse, where iterative rewriting erodes details over time."
2. 붕괴의 사례: 컨텍스트가 18,282 토큰에서 122 토큰으로 줄자 정확도가 66.7에서 57.1로 떨어졌고, 이는 컨텍스트 없음(63.7)보다도 낮다.
   - 원문(p3): "# Tokens: 18,282 Accuracy: 66.7 Accuracy w/o context: 63.7 # Tokens: 122 Accuracy: 57.1" 그리고 "Monolithic rewriting of context by an LLM can collapse it into shorter, less informative summaries, leading to sharp performance drops."
3. 대안은 통째 다시 쓰기 대신 부분 수정과 점진 증가다.
   - 원문(p4): "incremental delta updates (§3.1) that replace costly monolithic rewrites with localized edits" 그리고 "a grow-and-refine mechanism (§3.2) that balances steady context expansion with redundancy control."
   - 설명: 정답 신호가 있는 환경에서 AppWorld 평균이 GPT-5.1 기준 개선되었다(p17 표 6). 부분 수정이 지연도 평균 86.9% 줄였다고 보고한다(p6).

relay 설계에 주는 함의: 가장 직접적인 반박·수정 근거. state.md를 180~250단어로 매번 새로 쓰는 방식은 이 논문이 경고한 "통째 요약 재작성"과 닮았다. 제안: (a) state.md는 매번 처음부터 쓰지 말고 항목 단위로 추가·삭제(delta)하며, (b) 세부는 archive에 남기고 state.md에는 위치 포인터만 둔다, (c) 결정 목록처럼 누적되어야 할 것은 줄이지 않는다. 한계: ACE의 대상은 에이전트가 쓰는 "전략 수첩"이고 인수인계 상태 파일이 아니다. 인수인계에서 붕괴가 같은 정도로 일어나는지는 이 논문이 측정하지 않았다.

## P8. LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory

- 서지: Di Wu, Hongwei Wang, Wenhao Yu, Yuwei Zhang, Kai-Wei Chang, Dong Yu. Published as a conference paper at ICLR 2025. arXiv:2410.10813v2, 2025-03-04. https://arxiv.org/abs/2410.10813 (파일 `papers/longmemeval.pdf`, 28쪽)

핵심 주장
1. 긴 상호작용 기억에서 상용 도우미와 긴 맥락 모델의 정확도가 크게 떨어진다.
   - 원문(p1): "commercial chat assistants and long-context LLMs showing a 30% accuracy drop on memorizing information across sustained interactions."
2. 대화를 요약·사실 추출로 압축하면 정보가 사라진다. 세션을 라운드(사용자 한 번+도우미 한 번) 단위로 나눠 저장하는 편이 낫다.
   - 원문(p7): "compressing sessions into summaries or user-specific facts, as seen in prior work such as Zhong et al. (2024) and Du et al. (2024), can lead to information loss, harming the performance of the system to answer detailed questions."
3. 색인(indexing)·검색(retrieval)·읽기(reading) 3단계로 나누고, 사실로 키 확장, 시간 인지 질의 확장이 검색을 개선한다. 완벽히 검색해도 활용은 쉽지 않다.
   - 원문(p1): "session decomposition for value granularity, fact-augmented key expansion for indexing, and time-aware query expansion for refining the search scope."
   - 원문(p2): "Even with perfect memory recall, accurately utilizing retrieved items is non-trivial."

relay 설계에 주는 함의: archive/recall을 지지하되 색인 설계 지침을 준다. 색인은 요약본이 아니라 원문 줄 단위로 만들고, 날짜(시간)를 붙이고, 사실 키를 보태라. 검색된 줄을 읽게 할 때 형식을 구조화하는 편이 낫다(같은 논문 p2 Chain-of-Note, 구조화 형식 최대 10점 향상). 한계: 사용자-도우미 잡담형 기억 벤치마크이고 작업 인수인계와는 과제가 다르다.

## P9. SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering

- 서지: John Yang, Carlos E. Jimenez, Alexander Wettig, Kilian Lieret, Shunyu Yao, Karthik Narasimhan, Ofir Press. arXiv:2405.15793v3, 2024-11-11. 게재처는 첫 쪽에 없음(NeurIPS 2024로 알려져 있으나 확인 안 함). https://arxiv.org/abs/2405.15793 (파일 `papers/sweagent.pdf`, 118쪽)

핵심 주장
1. 에이전트에게는 사람용 셸이 아니라 에이전트용 인터페이스(agent-computer interface, ACI)가 필요하다.
   - 원문(p2): "ACIs tailored specifically for LMs outperform existing user interfaces (UIs) designed for human users, such as the Linux shell."
2. 편집 시 검사 장치(linter)로 잘못된 편집을 버리게 하면 성능이 크게 오른다.
   - 원문(p4): "Invalid edits are discarded, and the agent is asked to try editing the file again."
   - 원문(p7): "This intervention improves performance considerably (without linting, 15.0% ↓ 3.0 )."
3. 동작은 단순하고 결과는 간결하게, 맥락을 짧고 정보 있게 유지한다.
   - 원문(p3): "Actions should be simple and easy to understand for agents." 그리고 p4: "The SWE-agent system uses informative prompts, error messages, and history processors to keep agent context concise and informative."

relay 설계에 주는 함의: 기계 검사기(checker)로 판정하는 설계를 지지하고, 작업 카드의 허용·금지 경로 같은 가드레일을 지지한다. 검사기·done_when의 출력은 "통과/실패와 짧은 원인"처럼 간결해야 한다는 보완. 또한 이력 처리기(history processor)로 오래된 관찰을 줄이는 것은 세션 내 수동 압축의 자동판이므로 relay의 수동 압축과 병행 가능하다.

## P10. Measuring AI Ability to Complete Long Software Tasks

- 서지: Thomas Kwa, Ben West, Joel Becker 외 (Model Evaluation & Threat Research, METR). arXiv:2503.14499v4, 2026-07-10. 게재처는 첫 쪽에 없음. https://arxiv.org/abs/2503.14499 (파일 `papers/metr.pdf`, 45쪽)

핵심 주장
1. 모델이 50% 신뢰도로 끝낼 수 있는 작업 길이(시간 지평, time horizon)가 약 7개월마다 두 배가 된다.
   - 원문(p1): "frontier AI time horizon has doubled approximately every seven months since 2019, though the trend may have accelerated since 2024."
2. 80% 신뢰도로 보면 지평이 4~6배 짧다. 중간 길이 작업도 믿고 맡기기 어렵다.
   - 원문(p7): "models’ 80% time horizons are 4-6x shorter, suggesting that even models that sometimes succeed on difficult and diverse tasks cannot reliably perform tasks of moderate length."
3. 피드백 고리가 분명하지 않은 지저분한(messier) 환경에서 약하다.
   - 원문(p7): "AI agents still seem to struggle in intuitively “messier” environments— specifically, environments without clear feedback loops, or where the agent needs to proactively seek out relevant information."

relay 설계에 주는 함의: 긴 일을 짧은 카드로 쪼개고 카드마다 기계적 완료 조건을 두는 설계를 지지한다(작업이 짧을수록 믿을 만하고, 피드백 고리가 명확해야 잘 된다). 모델이 빠르게 좋아지고 있으므로 카드 크기 기준(180~250단어 등 수치)은 고정하지 말고 주기적으로 재검토하라는 시사도 있다. 한계: 모델별 분담(싼 모델/비싼 모델)을 직접 검증한 논문은 아니다.

## 확인 못 한 것과 한계

- 학회 게재처 중 UIST 2023(P2), ICLR 2025(P8), ICLR 2026(P7), TMLR 2024(P5)는 PDF 첫 쪽에 표기가 있어 확인함. NeurIPS 2023(P3), TACL 2024(P4), SWE-agent·Voyager의 게재처는 PDF 첫 쪽에 없어 확인 안 함. 표에는 확인된 연도만 확정으로 적었다.
- 모델별 분담(싼 모델 탐색, 비싼 모델 판단)과 멀티 에이전트 인수인계를 직접 다룬 논문은 이번 10편에 넣지 못했다. 근거가 약한 설계 요소다.
- Agent Workflow Memory, A-MEM은 원문 확인 없이 제외했다(10편 제한).
- 인용 쪽 번호는 PDF 쪽 순서이며 논문 자체의 인쇄 쪽과 다를 수 있다. 수치(예: ACE의 66.7→57.1)는 원문 그림 안의 텍스트를 그대로 옮긴 것이다.
