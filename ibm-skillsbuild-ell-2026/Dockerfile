# ASCE GovBen Compliance Gateway
# Base: Red Hat Universal Base Image 9 (UBI) - required for Red Hat container certification
FROM registry.access.redhat.com/ubi9/python-311:latest

USER 0

# Red Hat certification expects license terms under /licenses
RUN mkdir -p /licenses
COPY LICENSE /licenses/LICENSE

WORKDIR /opt/app-root/src

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY --chown=1001:0 . .
RUN chmod -R g=u /opt/app-root/src

# Labels required for Red Hat Partner Connect & Preflight Certification
LABEL name="asce-govben-gateway" \
      vendor="ASCE Technologies LLC" \
      maintainer="Todd Bullitt" \
      version="3.1" \
      release="1" \
      summary="ASCE GovBen compliance decision-support gateway" \
      description="Synthetic-data demonstration of a human-in-the-loop statutory compliance gateway using IBM watsonx.ai. Utilizing deterministic logic gates for secure state transitions."

# OpenShift & Preflight mandates non-root execution
USER 1001

# Expose consistent port for application
EXPOSE 8080

# Health check to verify application is running
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD curl -f http://localhost:8080/api/health || exit 1

# Execute the GovBen state-machine web gateway with proper timeout and worker settings
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "90", "--workers", "2", "--worker-class", "sync", "--preload", "app:app"]