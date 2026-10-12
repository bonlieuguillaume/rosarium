# rosarium in a container: SNAP 13 (gpt) + the conda env + the code.
# Build and run through docker-compose.yml (see the README, "Docker").
#
# Layers go from the most stable to the most changing, so that editing the
# code only rebuilds the last ones (seconds) and never SNAP or the env.

# SNAP 13 by the SNAP team (Brockmann Consult): Ubuntu 24.04 + micromamba +
# SNAP in /opt/esa-snap. Pinned by digest: the only tag, `latest`, can move.
# To upgrade, replace the digest on purpose and re-test.
FROM quay.io/bcdev/snap13@sha256:10bb3484129d5f5f879c6f5293083880957230ad5dabdc5d9acf629f3a98461b

USER root

# --- SNAP module updates --------------------------------------------------
# The image ships SNAP 13.0.0 with module patches from April 2026
# (microwavetbx 13.0.3); the chain was validated on the later ones (13.0.5).
# Run as root they land in /opt/esa-snap, visible to every user. The `snap`
# launcher starts the NetBeans platform, which looks for a screen: without
# DISPLAY and in headless mode it does not. The check fails the build if a
# module the graphs use still has an update pending (i.e. --update-all failed).
RUN env -u DISPLAY /opt/esa-snap/bin/snap -J-Djava.awt.headless=true \
        --nosplash --nogui --modules --refresh --update-all \
 && env -u DISPLAY /opt/esa-snap/bin/snap -J-Djava.awt.headless=true \
        --nosplash --nogui --modules --list --refresh > /tmp/snap_modules.txt \
 && if grep -E '^(eu\.esa\.microwavetbx|org\.jlinda|org\.esa\.snap\.snap\.(core|gpf|collocation|dem|geotiff))\S* .*Upgrade to' /tmp/snap_modules.txt; then \
        echo "SNAP modules above still have an update pending" >&2; exit 1; \
    fi \
 && rm /tmp/snap_modules.txt

# gpt never opens a window: headless keeps Java from looking for the X
# display the base image points DISPLAY at. Its -Xmx10G there is overridden
# by the -J-Xmx rosarium passes on every call.
RUN echo "-Djava.awt.headless=true" >> /opt/esa-snap/bin/gpt.vmoptions
ENV DISPLAY=
ENV PATH=/opt/esa-snap/bin:$PATH

# --- Runtime user ---------------------------------------------------------
# Run as the image's ordinary user, not root. Its uid is set to the host
# user's (ROSARIUM_UID, 1000 = the first user on most Linux machines), so the
# files written into the mounted data/ belong to that user on Linux; on
# Docker Desktop (Windows, macOS) the uid makes no difference. Ubuntu's
# default `ubuntu` user holds 1000 and is removed first.
ARG ROSARIUM_UID=1000
ARG ROSARIUM_GID=1000
RUN (userdel -r ubuntu 2>/dev/null || true) \
 && groupmod -g "$ROSARIUM_GID" "$MAMBA_USER" \
 && usermod -u "$ROSARIUM_UID" -g "$ROSARIUM_GID" "$MAMBA_USER" \
 && chown -R "$ROSARIUM_UID:$ROSARIUM_GID" "/home/$MAMBA_USER"

# --- Python env -----------------------------------------------------------
# The same env_light_rosarium.yml as a conda install, into the image's base
# env (activated by the image's entrypoint; on the PATH for `docker exec`).
COPY env_light_rosarium.yml /tmp/env.yml
RUN micromamba install -y -n base -f /tmp/env.yml \
 && micromamba clean --all --yes \
 && rm /tmp/env.yml
ENV PATH=/opt/conda/bin:$PATH

# --- Code -----------------------------------------------------------------
WORKDIR /app
COPY --chown=$MAMBA_USER:$MAMBA_USER . /app

USER $MAMBA_USER
# SNAP's user directory: auxdata (DEM tiles, orbit files downloaded during
# the runs) is a volume in docker-compose.yml. Created here so that the
# volume takes this owner instead of root.
RUN mkdir -p "/home/$MAMBA_USER/.snap/auxdata"

EXPOSE 8050
# The webmap; it stops once the page is closed, and the container with it.
# Any other command replaces this one: `docker compose run --rm rosarium
# python rosarium.py download ...`
CMD ["python", "rosarium.py", "webmap", "--port", "8050"]
