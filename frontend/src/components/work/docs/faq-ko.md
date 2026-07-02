# 자주 묻는 질문

본 문서는 Open ACE 사용 중 사용자가 겪을 수 있는 일반적인 문제와 해결 방법을 정리했습니다.

---

## 목차

**一、설치 및 배포**
- Docker 시작 실패: 데이터베이스 연결 시간 초과
- 포트 충돌: 19888 포트 사용 중
- SECRET_KEY 미설정: 프로덕션 시작 실패
- 설정 파일 없음

**二、로그인 및 인증**
- 로그인 실패: 사용자 이름 또는 비밀번호 오류
- 세션 만료: 자동 로그아웃
- 계정 비활성: 로그인 불가
- 권한 부족: 관리 기능 접근 불가
- 비밀번호 변경 실패: 비밀번호 길이 부족

**三、작업 공간 및 프로젝트 관리**
- 프로젝트 생성 실패: 경로 권한 부족
- 프로젝트 경로 없음 또는 접근 불가
- 프로젝트 이미 존재: 중복 생성
- 멀티유저 모드 Workspace 시작 실패
- Workspace 인스턴스 수 한도 도달

**四、세션 및 AI 대화**
- 쿼터 초과: Workspace 비활성
- 원격 머신 오프라인: 원격 세션 생성 불가
- 요청 시간 초과 또는 네트워크 오류
- 세션 없음

**五、시스템 설정**
- 언어 전환
- 테마 전환 (어두운/밝은)
- 페이지 새로고침 후 설정丢失

---

## 一、설치 및 배포

### Docker 시작 실패: 데이터베이스 연결 시간 초과

**현상:** Docker 컨테이너 시작 시 로그 표시: `ERROR: PostgreSQL not ready after 60s. Exiting.`

**원인:**
1. PostgreSQL 컨테이너 초기화 미완료
2. 데이터베이스 연결 매개변수 설정 오류
3. Docker 네트워크 문제로 컨테이너 간 통신 불가

**해결 방법:**
1. PostgreSQL 컨테이너 상태 확인: `docker compose ps`
2. PostgreSQL 로그 확인: `docker compose logs postgres`
3. PostgreSQL 초기화 중인 경우 완료 대기 후 재시작: `docker compose restart open-ace-web`
4. 데이터베이스 연결 매개변수 확인

**예방 조치:** docker-compose.yml에 depends_on과 healthcheck 설정

---

### 포트 충돌: 19888 포트 사용 중

**현상:** 시작 시 오류: `Error: Address already in use (0.0.0.0:19888)`

**원인:**
1. 다른 서비스가 19888 포트 사용 중
2. 이전 Open ACE 프로세스가 완전히 종료되지 않음

**해결 방법:**
1. 포트 사용 확인: `lsof -i :19888` 또는 `netstat -tlnp | grep 19888`
2. 포트 사용 프로세스 종료: `kill -9 <PID>` 또는 `docker compose down`
3. 다른 포트로 시작: `PORT=8080 docker compose up -d`

---

### SECRET_KEY 미설정: 프로덕션 시작 실패

**현상:** 컨테이너 시작 실패, 로그 표시: `RuntimeError: SECRET_KEY environment variable must be set in production!`

**원인:**
1. 프로덕션 환경에서 SECRET_KEY 환경 변수 미설정
2. 기본 개발 키 사용

**해결 방법:**
1. SECRET_KEY 환경 변수 설정: `echo "SECRET_KEY=$(openssl rand -hex 32)" > .env`
2. docker-compose.yml에서 환경 변수 설정
3. 컨테이너 재시작

---

### 설정 파일 없음

**현상:** 시작 후 Workspace 기능 사용 불가, 로그 표시: `Config file not found: ~/.open-ace/config.json`

**해결 방법:**
1. 설정 디렉터리와 파일 생성: `mkdir -p ~/.open-ace` 후 예제 설정 복사
2. 설정 파일 편집으로 host_name 등 매개변수 변경
3. 서비스 재시작

---

## 二、로그인 및 인증

### 로그인 실패: 사용자 이름 또는 비밀번호 오류

**현상:** 로그인 페이지에 "사용자 이름 또는 비밀번호가 올바르지 않습니다" 표시

**해결 방법:**
1. 첫 로그인은 기본 관리자 계정 사용: 사용자 이름 `admin`, 비밀번호 `admin123`
2. 기본 비밀번호 무효인 경우 관리자에게 비밀번호 재설정 요청
3. 사용자 존재 확인

---

### 세션 만료: 자동 로그아웃

**현상:** 사용 중 페이지가 자동으로 로그인 페이지로 이동

**원인:**
1. 세션 유효 기간 경과 (기본 24시간)
2. 브라우저 Cookie 삭제
3. 서비스 재시작으로 세션 무효화

**해결 방법:** 재로그인으로 복구

---

### 계정 비활성: 로그인 불가

**현상:** 로그인 실패, "Account is disabled" 표시

**해결 방법:** 관리자에게 계정 재활성화 요청:
```bash
docker compose exec postgres psql -U ace -d ace -c "UPDATE users SET is_active=true WHERE username='xxx';"
```

---

### 권한 부족: 관리 기능 접근 불가

**현상:** 관리 페이지 접근 시 "Admin access required" 표시

**해결 방법:**
1. 현재 사용자 권한 확인
2. 관리자에게 사용자 권한을 admin으로 변경 요청

---

### 비밀번호 변경 실패: 비밀번호 길이 부족

**현상:** 비밀번호 변경 시 "New password must be at least 8 characters" 표시

**해결 방법:**
1. 새 비밀번호 8자 이상 확인
2. 새 비밀번호와 현재 비밀번호가 다른지 확인

---

## 三、작업 공간 및 프로젝트 관리

### 프로젝트 생성 실패: 경로 권한 부족

**현상:** 프로젝트 생성 시 "Permission denied to create directory" 표시

**해결 방법:**
1. 사용자 system_account 권한 확인: `sudo chown -R <user>:<group> /path`
2. 권한 부여 또는 다른 경로 사용
3. 멀티유저 모드 기본 경로: `/workspace/<username>/`

---

### 프로젝트 경로 없음 또는 접근 불가

**현상:** 프로젝트 열기 시 "Directory does not exist" 표시

**해결 방법:**
1. 경로 존재와 디렉터리 확인
2. 경로 없는 경우 프로젝트 재생성

---

### 프로젝트 이미 존재: 중복 생성

**현상:** 프로젝트 생성 시 "Project already exists" 표시

**해결 방법:** 다른 경로로 새 프로젝트 생성, 또는 기존 프로젝트 삭제 후 재생성

---

### 멀티유저 모드 Workspace 시작 실패

**현상:** 작업 공간 진입 시 "Failed to get user workspace URL" 표시

**원인:**
1. qwen-code-webui 미설치 또는 경로 설정 오류
2. 사용자 system_account 시스템 사용자 없음
3. sudo 설정 문제

**해결 방법:**
1. qwen-code-webui 사용 가능 확인: `which qwen-code-webui`
2. 사용자 system_account 존재 확인: `id <account>`
3. sudoers 설정 확인
4. 시작 로그 확인: `tail -f /tmp/open-ace-*.log`

---

### Workspace 인스턴스 수 한도 도달

**현상:** 새 세션 생성 시 "Maximum instances (30) reached" 표시

**해결 방법:**
1. 대기 중 인스턴스 자동 정리 대기 (기본 30분 타임아웃)
2. 관리자 설정 변경으로 한도 증가 (max_instances)

---

## 四、세션 및 AI 대화

### 쿼터 초과: Workspace 비활성

**현상:** 작업 공간에 쿼터 초과 경고 표시, AI 기능 사용 불가

**원인:**
1. 일/월 Token 사용량이 쿼터 한도 초과
2. 일/월 요청 수가 쿼터 한도 초과

**해결 방법:**
1. Dashboard 페이지에서 Usage Overview 확인
2. 쿼터 재설정 대기 (일 쿼터 매일 재설정, 월 쿼터 매월 재설정)
3. 관리자에게 쿼터 조정 요청

---

### 원격 머신 오프라인: 원격 세션 생성 불가

**현상:** 원격 세션 생성 시 "Failed to create remote session" 표시

**원인:**
1. 원격 Agent 미실행 또는 네트워크 연결 불가
2. Agent 등록 만료
3. 사용자가 머신에 미배정

**해결 방법:**
1. 원격 머신 상태 확인
2. Agent 서비스 실행 확인: `systemctl status open-ace-agent`
3. Agent 재등록
4. 사용자가 머신에 배정 확인

---

### 요청 시간 초과 또는 네트워크 오류

**현상:** API 요청 실패, "Request timed out" 또는 "Network error" 표시

**원인:**
1. 네트워크 연결 불안정
2. 서버 응답 지연 또는 고부하
3. 요청 타임아웃 (기본 30초)

**해결 방법:**
1. 네트워크 연결 상태 확인
2. 페이지 새로고침으로 재시도 (프론트엔드 자동 재시도 3회)
3. 서비스 상태 확인

---

### 세션 없음

**현상:** 세션 상세 열기 시 "Session not found" 표시

**원인:**
1. 세션 삭제 또는 만료
2. 세션 ID 오류
3. 사용자 접근 권한 없음

**해결 방법:**
1. 세션 ID 정확성 확인
2. 세션 목록에서 유효 세션 검색
3. 원격 세션인 경우 원격 머신 온라인 확인

---

## 五、시스템 설정

### 언어 전환

**해결 방법:** 로그인 페이지 또는 설정 페이지에서 언어 선택. 지원 언어:
- English (영어)
- 中文 (중국어 간체)
- 日本語 (일본어)
- 한국어 (한국어)

---

### 테마 전환 (어두운/밝은)

**해결 방법:** 인터페이스 상단 또는 설정에서 테마 전환 버튼 찾기, Light / Dark 모드 선택

---

### 페이지 새로고침 후 설정丢失

**원인:** 브라우저가 로컬 스토리지 비활성화

**해결 방법:** 브라우저가 localStorage 사용 허용 확인, 설정 재구성

---

## 더 많은 도움

위 해결 방법으로 문제가 해결되지 않으면:
1. GitHub Issues에서 관련 문제 확인: https://github.com/open-ace/open-ace/issues
2. 새 Issue 제출, 문제 설명, 재현 단계, 환경 정보, 관련 로그 포함
