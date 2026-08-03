# Dev-образ фронта. Зависимости ставятся при запуске (см. command в docker-compose.yml)
# в постоянный volume, а не вшиваются в образ — тогда после добавления пакета
# достаточно `docker-compose up`, без ручной пересборки образа.
# Прод собирается отдельно — deploy/Dockerfile.web.
FROM node:20-alpine

WORKDIR /app

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host"]
