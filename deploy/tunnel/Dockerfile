# Мини-образ туннеля: держит SSH-подключение к зарубежному серверу и отдаёт
# SOCKS5-прокси на порт 1080 внутри docker-сети (для OpenRouter из РФ).
FROM alpine:3.20
RUN apk add --no-cache autossh openssh-client
# Команда запуска задаётся в docker-compose (использует TUNNEL_SSH_* из .env).
