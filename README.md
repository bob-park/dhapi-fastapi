# dhapi 를 FastAPI 로 구현한 API 서버

## docker compose
```yaml
services:
  dhapi:
    image: ghcr.io/bob-park/dhapi-fastapi
    ports:
      - "8000:8000"
    environment:
      - USERNAME=${username}
      - PASSWORD=${password} 
```
