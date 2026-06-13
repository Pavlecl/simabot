#!/bin/bash
set -e

echo "⬇️  Получаем изменения..."
cd ~/sima_bot
git pull origin main

echo "🔨 Пересобираем образы..."
docker compose build bot web
# ℹ️  Без --no-cache: Docker использует кэш слоёв.
# pip install запустится только если изменился requirements.txt
# Базовый образ python:3.10-slim берётся из локального кэша

echo "🔄 Перезапускаем сервисы..."
docker compose up -d --no-deps bot web

echo "🧹 Чистим старые образы..."
docker image prune -f

echo "✅ Готово!"
docker compose ps
