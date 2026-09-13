# Stage 1: Build static distribution
FROM node:22-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

ARG VITE_ENGINE_URL=http://localhost:8000
ENV VITE_ENGINE_URL=$VITE_ENGINE_URL

RUN npm run build

# Stage 2: Serve static files with Nginx
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
