# Le SPA, construit puis servi en fichiers statiques — par la stack Portainer
# deploy/ea.stack.yml, derrière le NGINX de l'Infra. Voir docs/adr/0027.

FROM node:26.12-alpine@sha256:6f08a523443ddedf52941874c203475b0ad503e859ad70cb26ab8be9a517dbd5 AS build

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

FROM nginx:1.31.6-alpine@sha256:df221db836e1754089190208cee7eeda94f233197056426eda74a43ab1abeac2

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
