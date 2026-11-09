# The site's container: static-web-server plus the built site, and nothing else.
#
# The image does not build Hugo. CI builds the site and runs scripts/check_build.py on
# that exact output first, then copies it in here, so the files served are the files that
# were checked. Build it the same way locally:
#
#     mise exec -- hugo --gc --minify && python scripts/check_build.py public
#     docker build -t site . && docker run --rm -p 8080:8080 site
#     python scripts/smoke.py http://127.0.0.1:8080
#
# The base image is static-web-server's own, built FROM scratch: one static binary, no
# shell, no package manager. Pinned by digest, so a re-pushed tag cannot change what is
# deployed; Dependabot proposes digest updates.
FROM ghcr.io/static-web-server/static-web-server:2.44.1@sha256:94618e3d17802265a21926217e6f9e7832b311b6fbf5933684001739fafed06a

COPY server/sws.toml /etc/sws.toml
COPY public/ /public/

ENV SERVER_CONFIG_FILE=/etc/sws.toml

# The base image runs as root. Nothing here needs it: the server only reads files and
# listens on 8080, an unprivileged port.
USER 65534:65534

EXPOSE 8080
