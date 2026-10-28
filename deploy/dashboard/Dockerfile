FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DASHBOARD_ONLY=true
WORKDIR /app
# Explicit allowlist: no credentials, research, trading modules or host data in image.
COPY --chmod=0444 server.py dashboard_data.py ./
COPY --chmod=0444 scripts/update_data.py scripts/collect_dashboard.py ./scripts/
RUN chmod 0755 /app /app/scripts
USER 10001:10001
CMD ["python", "server.py"]
