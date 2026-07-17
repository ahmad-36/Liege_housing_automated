# Liège Housing Finder — Hugging Face Spaces (Docker SDK) / any container host
FROM python:3.12-slim

RUN useradd -m -u 1000 user
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright Chromium + its system libraries, installed as root in a
# shared location so the non-root runtime user can use it.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN playwright install --with-deps chromium && chmod -R a+rX /opt/pw-browsers

COPY --chown=user . /app
USER user
ENV HOME=/home/user

EXPOSE 7860
CMD ["streamlit", "run", "app.py", \
     "--server.port=7860", "--server.address=0.0.0.0", \
     "--server.headless=true", "--browser.gatherUsageStats=false", \
     "--server.enableXsrfProtection=false"]
