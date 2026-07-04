FROM mcr.microsoft.com/playwright/python:v1.56.0-jammy

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt

COPY wg_monitor /app/wg_monitor
COPY run_server.py /app/run_server.py
COPY config /app/config
COPY examples /app/examples

ENV APP_ROOT=/app
ENV HOST=0.0.0.0
ENV PORT=8000
ENV RUN_BACKGROUND=true
ENV QUESTIONNAIRE_HELPER_DIR=/app/questionnaire_helper

EXPOSE 8000
CMD ["python", "run_server.py"]
