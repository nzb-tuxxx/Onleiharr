FROM python:alpine AS builder
WORKDIR /build
COPY . /build/
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

FROM python:alpine
WORKDIR /app
COPY --from=builder /wheels /tmp/wheels
RUN pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels
VOLUME ["/config"]
ENTRYPOINT ["onleiharr"]
CMD ["-c", "/config/onleiharr.toml"]
