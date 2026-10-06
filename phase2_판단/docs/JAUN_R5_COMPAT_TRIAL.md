# 자운 + r5 호환성 시험본 (미커밋)

기반: Git 58726de(내용상 26aa17f), 기존 r5 generalization/claim-linkage 후보, 철회한 b6cbe4b의 시험용 보완.
원본 Git 및 후보는 수정하지 않았다. 이 폴더는 운영본이 아니다.

수동 해결: checklist_adapter 설명, grounding 프롬프트 버전 및 양쪽 지침, context v0.2 테스트, CLI context-max 옵션. 조직 프로필 전달·해시와 기존 span 인용·문맥 선택·의미 guard·Self-check 유지.
시험: compatibility는 기존 25 + 새 프로필 5 사례. actual은 기존 16문항. 255문항은 실행하지 않는다.
프로필 기대값은 개발 참조이며 기준팀 승인 정답이 아니다. ACCESS-ROLE-ALIGNED 기준 대기 및 승인시점 설명 오류는 해결됐다고 선언하지 않는다.
Self-check는 audit.self_check에 저장된다. verdict만 아니라 설명·인용·사유 코드·프로필 해시도 확인한다.
실제 Ollama 결과 미확인. Git commit/push 하지 않음.
