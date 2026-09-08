# AMA-12 — CI 비밀값 검사

`python scripts/check_secrets.py`는 Git 추적 파일의 현재 내용을 바이트로 검사한다.
CI는 Python 준비 직후, 의존성 설치 전에 이 검사를 실행한다. 검출 시 exit 1,
Git/파일 읽기 실패나 안전하지 않은 링크는 exit 2로 실패하며 검사를 생략하지 않는다.
성공은 exit 0이다. 런타임 비밀값 디렉터리와 추적하지 않는 `.env`는 읽지 않는다.

검사 범위는 private key header, AWS/GitHub/Google/Slack/OpenAI credential 패턴과
긴 quoted client_secret/api_key/access_token/refresh_token/password literal이다.
대문자 환경변수 이름이 정확히 같은 이름의 `_SECRET` 환경변수를 가리키는
Secret Manager 매핑은 값이 아닌 참조로 구분한다. 다른 긴 literal은 계속 검사한다.
출력은 파일 경로·줄 번호·규칙 이름뿐이며 비밀값이나 원문 행을 출력하지 않는다.
테스트용 credential도 예외 처리하지 않는다. 정상/실패 테스트는 가짜 패턴을
실행 중 조합하고 임시 Git 저장소에서 차단 여부와 로그 비노출을 검증한다.

이 검사는 현재 추적 파일에 대한 패턴 검사다. Git 과거 이력, 미추적 파일,
암호화/분할된 값, 지원하지 않는 형식의 모든 비밀값을 탐지한다는 보장은 없다.
노출된 실제 credential을 발견하면 저장소 수정만으로 해결되지 않으며 해당
credential의 폐기·교체가 필요하다.

기반 CI의 다른 검사는 pytest의 계층 의존/설정/migration 검증, governance/API
계약 검사, wheel build와 격리 설치 smoke, shell syntax/ShellCheck다.
Python 전체 정적 타입 검사는 현재 범위에 포함하지 않는다.
