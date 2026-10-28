FROM node:22-bookworm-slim AS build
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@10.32.1 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps ./apps
COPY packages ./packages
COPY tsconfig.json ./
RUN pnpm install --frozen-lockfile && pnpm build

FROM node:22-bookworm-slim
WORKDIR /app
COPY --from=build /app/apps/shell/dist ./apps/shell/dist
COPY server ./server
ENV ICARUS_WEBUI_HOST=0.0.0.0 ICARUS_WEBUI_PORT=8080
USER node
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD node -e "fetch('http://127.0.0.1:8080/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "server/index.mjs"]
