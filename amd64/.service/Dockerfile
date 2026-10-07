# linux/amd64 variant. nodo builds it with --opt platform=linux/amd64 (from
# service.json). The python base images are multi-arch indexes, and every
# native wheel used has a cp311 manylinux build for x86_64 and aarch64.

# Only the last stage becomes the service filesystem. The build stage has git,
# which pip needs for the pinned bee-rpc commit.
# Both base images are multi-arch indexes, pinned by digest.
FROM python:3.11@sha256:27e044f7e01fea05c1760324d58fc5360a0767b9ef098e74ddaf8c70b8f46d26 AS builder
COPY ./requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r /tmp/requirements.txt

FROM python:3.11-slim@sha256:6f31d6e9ba2b0a787a3f81c37b004155b87b9efa1b771182bd550c1615745be5
COPY --from=builder /install /usr/local
# The service directory also has the dependencies that the packer adds:
# __services__, __metadata__, __block__ and .dependencies.
COPY service /satsorter
RUN chmod +x /satsorter/start.sh \
    && cd /satsorter && python3 -B -c "import src.main"
