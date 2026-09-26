# Le SPA, construit puis servi en fichiers statiques — par la stack Portainer
# deploy/ea.stack.yml, derrière le NGINX de l'Infra. Voir docs/adr/0027.

FROM node:26.10-alpine@sha256:0b36e8c136b94cd4fcf02188228e76c31ad5872eef3fec8cbd2eee500cfd9e80 AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .

# Vite l'écrit dans le bundle au build : c'est un argument de build, pas une
# variable d'exécution. Relatif, donc valable sous n'importe quel nom d'hôte :
# le vhost sert l'API sous /api, sur la même origine que le SPA.
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
# Le realm et le client public du SPA (docs/adr/0032). La stack les repasse en
# arguments ; les défauts sont ceux du déploiement de l'Infra.
ARG VITE_AUTH_AUTHORITY=https://keycloak.famillelallier.net/realms/ea
ARG VITE_AUTH_CLIENT_ID=ea-spa
ENV VITE_AUTH_AUTHORITY=$VITE_AUTH_AUTHORITY
ENV VITE_AUTH_CLIENT_ID=$VITE_AUTH_CLIENT_ID
RUN npm run build

FROM nginx:1.31.5-alpine@sha256:72ba65eb42c10344912a84ff42408db7d34f2feb642204570ab8fc5ffd29f1d3

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
