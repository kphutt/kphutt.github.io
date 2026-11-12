# The explorables container: static-web-server plus the checked explorables, nothing else.
#
# The same pattern as the site's Dockerfile. CI copies explorables/ into public-explorables/
# without its READMEs, adds the generated robots.txt and sitemap.xml, runs
# scripts/check_explorables.py on that exact output, then copies it in here. Build it the
# same way locally:
#
#     python scripts/build_explorables.py && python scripts/check_explorables.py public-explorables
#     docker build -f explorables.Dockerfile -t explorables . && docker run --rm -p 8080:8080 explorables
#     python scripts/smoke_explorables.py http://127.0.0.1:8080
#
# The base image must stay the one the site's Dockerfile pins, digest included.
# scripts/check_explorables.py fails if the two FROM lines differ, so a Dependabot update
# to one has to be carried to the other.
FROM ghcr.io/static-web-server/static-web-server:2.44.1@sha256:94618e3d17802265a21926217e6f9e7832b311b6fbf5933684001739fafed06a

COPY server/explorables.sws.toml /etc/sws.toml
COPY public-explorables/ /public/

ENV SERVER_CONFIG_FILE=/etc/sws.toml

USER 65534:65534

EXPOSE 8080
