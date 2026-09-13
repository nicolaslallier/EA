# Le SPA, construit puis servi en fichiers statiques — par la stack Portainer
# deploy/ea.stack.yml, derrière le NGINX de l'Infra. Voir docs/adr/0027.

FROM node:22.23.2-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .

# Vite l'écrit dans le bundle au build : c'est un argument de build, pas une
# variable d'exécution. Relatif, donc valable sous n'importe quel nom d'hôte :
# le vhost sert l'API sous /api, sur la même origine que le SPA.
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginx:1.31.5-alpine@sha256:72ba65eb42c10344912a84ff42408db7d34f2feb642204570ab8fc5ffd29f1d3

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
