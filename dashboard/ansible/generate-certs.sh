#!/bin/bash

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Create a temporary directory for certificates
CERT_DIR="./certs"
rm -rf $CERT_DIR
mkdir -p $CERT_DIR

# Generate CA private key and public certificate
openssl genrsa -out $CERT_DIR/ca-key.pem 4096
openssl req -new -x509 -days 365 -key $CERT_DIR/ca-key.pem -sha256 -out $CERT_DIR/ca.pem -subj "/CN=docker-ca"

# Generate server key and certificate signing request (CSR)
openssl genrsa -out $CERT_DIR/server-key.pem 4096
openssl req -subj "/CN=$DROPLET_IP" -sha256 -new -key $CERT_DIR/server-key.pem -out $CERT_DIR/server.csr

# Generate client key and certificate signing request (CSR)
openssl genrsa -out $CERT_DIR/key.pem 4096
openssl req -subj '/CN=client' -new -key $CERT_DIR/key.pem -out $CERT_DIR/client.csr

# Sign the server certificate
echo "subjectAltName = DNS:${DROPLET_IP},IP:${DROPLET_IP},IP:127.0.0.1" > $CERT_DIR/extfile.cnf
echo "extendedKeyUsage = serverAuth" >> $CERT_DIR/extfile.cnf
openssl x509 -req -days 365 -sha256 -in $CERT_DIR/server.csr -CA $CERT_DIR/ca.pem -CAkey $CERT_DIR/ca-key.pem -CAcreateserial -out $CERT_DIR/server-cert.pem -extfile $CERT_DIR/extfile.cnf

# Sign the client certificate
echo "extendedKeyUsage = clientAuth" > $CERT_DIR/extfile-client.cnf
openssl x509 -req -days 365 -sha256 -in $CERT_DIR/client.csr -CA $CERT_DIR/ca.pem -CAkey $CERT_DIR/ca-key.pem -CAcreateserial -out $CERT_DIR/cert.pem -extfile $CERT_DIR/extfile-client.cnf

# Clean up CSR files
rm -f $CERT_DIR/server.csr $CERT_DIR/client.csr $CERT_DIR/extfile.cnf $CERT_DIR/extfile-client.cnf

# Try to set proper permissions, but don't fail if we can't
chmod 0600 $CERT_DIR/ca-key.pem $CERT_DIR/server-key.pem $CERT_DIR/key.pem 2>/dev/null || true
chmod 0644 $CERT_DIR/ca.pem $CERT_DIR/server-cert.pem $CERT_DIR/cert.pem 2>/dev/null || true

echo "Certificates generated successfully in $CERT_DIR" 