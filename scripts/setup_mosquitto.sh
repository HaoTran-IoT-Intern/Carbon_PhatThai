#!/usr/bin/env bash
set -euo pipefail

# Script thiết lập Local Mosquitto Broker cho Edge Gateway CEMS
# Tự động tạo thư mục cấu hình, file mật khẩu passwd và file phân quyền aclfile

MOSQUITTO_DIR="${MOSQUITTO_DIR:-/etc/mosquitto}"
PASSWD_FILE="${MOSQUITTO_DIR}/passwd"
ACL_FILE="${MOSQUITTO_DIR}/aclfile"

NODE_USER="${NODE_USER:-cems_node}"
NODE_PASS="${NODE_PASS:-cems_secret_pass}"

GW_USER="${GW_USER:-gateway}"
GW_PASS="${GW_PASS:-cems_gw_secret_pass}"

echo "[INFO] Thiết lập Mosquitto tại: ${MOSQUITTO_DIR}"

# Tạo thư mục nếu chưa có
mkdir -p "${MOSQUITTO_DIR}"
mkdir -p /var/lib/mosquitto 2>/dev/null || true

# Tạo file password
if command -v mosquitto_passwd >/dev/null 2>&1; then
    echo "[INFO] Sử dụng mosquitto_passwd để khởi tạo mật khẩu..."
    mosquitto_passwd -b -c "${PASSWD_FILE}" "${NODE_USER}" "${NODE_PASS}"
    mosquitto_passwd -b "${PASSWD_FILE}" "${GW_USER}" "${GW_PASS}"
else
    echo "[WARN] Không tìm thấy lệnh mosquitto_passwd. Tạo file fallback..."
    echo "${NODE_USER}:${NODE_PASS}" > "${PASSWD_FILE}"
    echo "${GW_USER}:${GW_PASS}" >> "${PASSWD_FILE}"
fi
chmod 600 "${PASSWD_FILE}" 2>/dev/null || true

# Tạo file phân quyền ACL
echo "[INFO] Khởi tạo file ACL: ${ACL_FILE}..."
cat << 'ACL_EOF' > "${ACL_FILE}"
# Phân quyền cho CEMS Node (chỉ được publish lên topic của node)
user cems_node
topic write pione/carbon/v1/+/data

# Phân quyền cho Edge Gateway (đọc dữ liệu toàn trạm)
user gateway
topic read pione/carbon/v1/+/data

# Mặc định cho phép subscribe
pattern read pione/carbon/v1/+/data
ACL_EOF
chmod 644 "${ACL_FILE}" 2>/dev/null || true

echo "[SUCCESS] Hoàn tất cấu hình Mosquitto Broker."
echo "  - Password file: ${PASSWD_FILE}"
echo "  - ACL file:      ${ACL_FILE}"
echo "  - Users:         ${NODE_USER}, ${GW_USER}"
