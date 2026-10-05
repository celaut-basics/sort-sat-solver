# Only the last stage becomes the service filesystem. The build stage has git,
# which pip needs for the pinned bee-rpc commit.
# Both base images are multi-arch indexes, pinned by digest.
FROM python:3.11@sha256:27e044f7e01fea05c1760324d58fc5360a0767b9ef098e74ddaf8c70b8f46d26 AS builder
COPY ./requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r /tmp/requirements.txt

FROM python:3.11-slim@sha256:6f31d6e9ba2b0a787a3f81c37b004155b87b9efa1b771182bd550c1615745be5
COPY --from=builder /install /usr/local
COPY service /regresioncnf
RUN chmod +x /regresioncnf/start.sh \
    && cd /regresioncnf && python3 -B -c "import start"
