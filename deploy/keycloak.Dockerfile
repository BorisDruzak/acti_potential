# Keycloak 26.8.1 linux/amd64. Build once; runtime root filesystem is read-only.
FROM quay.io/keycloak/keycloak@sha256:ed1cab21814c7d18ce8f0e11d5a1fa1c6923de12dd4c7625a18c5d34452a57a4
RUN /opt/keycloak/bin/kc.sh build --db=postgres --health-enabled=true
