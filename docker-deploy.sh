#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-${PROJECT_DIR}/docker-compose.linux.yml}"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
IMAGE_ARCHIVE="${IMAGE_ARCHIVE:-${PROJECT_DIR}/xianyu-auto-reply-linux-amd64.tar}"
MIN_HOST_FREE_KIB="${MIN_HOST_FREE_KIB:-6291456}"

compose() {
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

info() {
    printf '%s\n' "$*"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

get_env() {
    local key="$1"
    local value
    value="$(grep -E "^[[:space:]]*${key}=" "${ENV_FILE}" | tail -n 1 | cut -d= -f2- | tr -d '\r' || true)"
    printf '%s' "${value}"
}

expected_image() {
    get_env XIANYU_IMAGE
}

validate_secret() {
    local key="$1"
    local min_length="$2"
    local value
    value="$(get_env "${key}")"
    [ -n "${value}" ] || fail "Missing ${key} in ${ENV_FILE}"
    case "${value}" in
        *CHANGE_ME*|*your_*|*replace_with*) fail "Replace placeholder value for ${key}" ;;
    esac
    [ "${#value}" -ge "${min_length}" ] || fail "${key} must be at least ${min_length} characters"
}

check_host() {
    [ "$(id -u)" -eq 0 ] || fail "Run Linux production operations with sudo so UID 10001 volumes stay private"
    require_command docker
    docker info >/dev/null 2>&1 || fail "Docker daemon is not available"
    docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"
    [ "$(uname -m)" = "x86_64" ] || fail "This deployment package expects an x86_64 host"
    local free_kib
    free_kib="$(df -Pk "${PROJECT_DIR}" | awk 'NR==2 {print $4}')"
    [ "${free_kib}" -ge "${MIN_HOST_FREE_KIB}" ] || fail "At least 6 GiB free disk space is required before deployment"
}

init_config() {
    [ "$(id -u)" -eq 0 ] || fail "Run Linux production operations with sudo so UID 10001 volumes stay private"
    [ -f "${PROJECT_DIR}/.env.linux.example" ] || fail ".env.linux.example is missing"
    if [ ! -f "${ENV_FILE}" ]; then
        cp "${PROJECT_DIR}/.env.linux.example" "${ENV_FILE}"
        chmod 600 "${ENV_FILE}"
        info "Created ${ENV_FILE}. Fill all CHANGE_ME values, then run: $0 deploy"
    else
        chmod 600 "${ENV_FILE}"
        info "Configuration already exists: ${ENV_FILE}"
    fi
    mkdir -p "${PROJECT_DIR}/data" "${PROJECT_DIR}/logs" "${PROJECT_DIR}/backups" "${PROJECT_DIR}/uploads/images"
    if [ "$(id -u)" -eq 0 ]; then
        chown -R 10001:10001 "${PROJECT_DIR}/data" "${PROJECT_DIR}/logs" "${PROJECT_DIR}/backups" "${PROJECT_DIR}/uploads"
    fi
    local directory
    for directory in data logs backups uploads/images; do
        [ "$(stat -c '%u:%g' "${PROJECT_DIR}/${directory}")" = "10001:10001" ] || \
            fail "${PROJECT_DIR}/${directory} must be owned by 10001:10001; run: sudo chown -R 10001:10001 '${PROJECT_DIR}/data' '${PROJECT_DIR}/logs' '${PROJECT_DIR}/backups' '${PROJECT_DIR}/uploads'"
        chmod 0750 "${PROJECT_DIR}/${directory}"
    done
}

validate_config() {
    [ -f "${ENV_FILE}" ] || fail "Run $0 init first"
    [ -f "${COMPOSE_FILE}" ] || fail "Compose file not found: ${COMPOSE_FILE}"
    [ -f "${PROJECT_DIR}/global_config.yml" ] || fail "global_config.yml is missing"
    local directory
    for directory in data logs backups uploads/images; do
        [ -d "${PROJECT_DIR}/${directory}" ] || fail "Missing runtime directory: ${PROJECT_DIR}/${directory}; run $0 init"
        [ "$(stat -c '%u:%g' "${PROJECT_DIR}/${directory}")" = "10001:10001" ] || \
            fail "${PROJECT_DIR}/${directory} must be owned by 10001:10001"
    done
    validate_secret XIANYU_IMAGE 3
    validate_secret ADMIN_USERNAME 1
    validate_secret ADMIN_PASSWORD 12
    validate_secret XIANYU_MESSAGE_API_KEY 32
    validate_secret DEEPSEEK_API_KEY 12
    validate_secret DASHSCOPE_API_KEY 12
    validate_secret FEISHU_APP_ID 8
    validate_secret FEISHU_APP_SECRET 16
    validate_secret FEISHU_BASE_TOKEN 8
    validate_secret FEISHU_TABLE_ID 6
    validate_secret FEISHU_INVENTORY_CHAT_ID 8
    validate_secret FEISHU_INVENTORY_SOURCE_MESSAGE_ID 8
    validate_secret FEISHU_INVENTORY_BRIDGE_TOKEN 32
    [ "$(get_env DB_PATH)" = "/app/data/xianyu_data.db" ] || fail "DB_PATH must remain /app/data/xianyu_data.db in Linux production"
    [ "$(get_env AI_CACHE_DIR)" = "/app/data" ] || fail "AI_CACHE_DIR must remain /app/data in Linux production"
    compose config --quiet
    info "Configuration validation passed"
}

verify_expected_image() {
    local image platform
    image="$(expected_image)"
    docker image inspect "${image}" >/dev/null 2>&1 || fail "Expected image tag was not loaded: ${image}"
    platform="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "${image}")"
    [ "${platform}" = "linux/amd64" ] || fail "Expected linux/amd64 image for ${image}, got ${platform}"
}

load_or_pull_images() {
    if [ -f "${IMAGE_ARCHIVE}" ]; then
        require_command sha256sum
        local expected_sha actual_sha
        expected_sha="$(get_env IMAGE_ARCHIVE_SHA256 | tr '[:upper:]' '[:lower:]')"
        [[ "${expected_sha}" =~ ^[0-9a-f]{64}$ ]] || fail "Set IMAGE_ARCHIVE_SHA256 to the archive's 64-character SHA256"
        actual_sha="$(sha256sum "${IMAGE_ARCHIVE}" | awk '{print $1}')"
        [ "${actual_sha}" = "${expected_sha}" ] || fail "Offline image archive SHA256 mismatch"
        info "Loading verified prebuilt image archive: ${IMAGE_ARCHIVE}"
        local load_output image
        image="$(expected_image)"
        load_output="$(docker load --input "${IMAGE_ARCHIVE}")"
        printf '%s\n' "${load_output}"
        printf '%s\n' "${load_output}" | grep -Fqx "Loaded image: ${image}" || \
            fail "Offline archive did not load the exact expected tag: ${image}"
    else
        info "Pulling prebuilt images"
        compose pull feishu-inventory-bridge xianyu-app maintenance
    fi
    verify_expected_image
}

wait_for_health() {
    local service="$1"
    local container="$2"
    local attempts="${3:-60}"
    local state
    for ((i=1; i<=attempts; i++)); do
        state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container}" 2>/dev/null || true)"
        case "${state}" in
            healthy|running)
                info "${service} is ${state}"
                return 0
                ;;
            unhealthy|exited|dead)
                compose logs --tail=80 "${service}" >&2 || true
                fail "${service} entered state ${state}"
                ;;
        esac
        sleep 2
    done
    compose logs --tail=80 "${service}" >&2 || true
    fail "Timed out waiting for ${service} health"
}

health_check() {
    wait_for_health feishu-inventory-bridge xianyu-feishu-bridge 30
    wait_for_health xianyu-app xianyu-auto-reply 60
    wait_for_health maintenance xianyu-maintenance 60
    compose exec -T feishu-inventory-bridge python /app/feishu_inventory_bridge.py --check
    compose exec -T maintenance python /app/production_maintenance.py health
    compose exec -T maintenance python /app/production_maintenance.py check-disk
    info "Production health check passed"
}

deploy() {
    check_host
    init_config
    validate_config
    load_or_pull_images
    compose up -d --no-build --remove-orphans
    health_check
    compose ps
    info "Deployment completed. Admin URL: http://$(get_env WEB_BIND_ADDRESS):$(get_env WEB_PORT)"
}

update() {
    check_host
    validate_config
    # Use the already-running maintenance container so an offline update does not
    # depend on the not-yet-loaded image tag from the updated .env file.
    compose exec -T maintenance python /app/production_maintenance.py backup
    backup_runtime_config

    local old_image_id rollback_tag image timestamp
    image="$(expected_image)"
    timestamp="$(date +%Y%m%d_%H%M%S)"
    old_image_id="$(docker inspect --format '{{.Image}}' xianyu-auto-reply 2>/dev/null || true)"
    [ -n "${old_image_id}" ] || fail "Cannot update before an existing xianyu-app deployment is running"
    rollback_tag="xianyu-auto-reply:rollback"
    docker image rm "${rollback_tag}" >/dev/null 2>&1 || true
    docker image tag "${old_image_id}" "${rollback_tag}"
    printf '%s %s %s\n' "${timestamp}" "${old_image_id}" "${image}" > "${PROJECT_DIR}/backups/pre_update_image_${timestamp}.txt"
    chmod 0600 "${PROJECT_DIR}/backups/pre_update_image_${timestamp}.txt"

    if ! (load_or_pull_images); then
        docker image tag "${rollback_tag}" "${image}"
        fail "Image preparation failed; the previous image tag was restored"
    fi
    if compose up -d --no-build --remove-orphans --force-recreate && (health_check); then
        compose images
        info "Update completed; rollback image retained as ${rollback_tag}"
        return 0
    fi

    info "Update failed; restoring ${old_image_id}"
    docker image tag "${rollback_tag}" "${image}"
    compose up -d --no-build --remove-orphans --force-recreate
    health_check || fail "Automatic rollback also failed; inspect service logs immediately"
    fail "Update failed and the previous image was restored"
}

backup_runtime_config() {
    local timestamp archive env_archive_name staging_dir
    timestamp="$(date +%Y%m%d_%H%M%S)"
    archive="${PROJECT_DIR}/backups/runtime_config_${timestamp}.tar.gz"
    staging_dir="$(mktemp -d "${PROJECT_DIR}/backups/.runtime-config-${timestamp}.XXXXXX")"
    trap 'rm -rf -- "${staging_dir}"' RETURN
    env_archive_name="$(basename -- "${ENV_FILE}")"
    cp -- "${ENV_FILE}" "${staging_dir}/${env_archive_name}"
    cp -- "${PROJECT_DIR}/global_config.yml" "${staging_dir}/global_config.yml"
    [ -f "${PROJECT_DIR}/data/token.txt" ] && cp -- "${PROJECT_DIR}/data/token.txt" "${staging_dir}/token.txt"
    [ -f "${PROJECT_DIR}/data/device_id.txt" ] && cp -- "${PROJECT_DIR}/data/device_id.txt" "${staging_dir}/device_id.txt"
    tar -C "${staging_dir}" -czf "${archive}" .
    chmod 600 "${archive}"
    rm -rf -- "${staging_dir}"
    trap - RETURN
    info "Runtime configuration backup completed: ${archive}"
}

backup() {
    [ "$(id -u)" -eq 0 ] || fail "Run Linux production operations with sudo so UID 10001 volumes stay private"
    validate_config
    compose run --rm --no-deps maintenance backup
    backup_runtime_config
}

restore() {
    local requested_archive="${2:-}"
    [ -n "${requested_archive}" ] || fail "Usage: $0 restore <archive>"
    check_host
    validate_config
    require_command realpath

    local archive_path restore_root staged_root timestamp snapshot
    archive_path="$(realpath -- "${requested_archive}")"
    [ -f "${archive_path}" ] || fail "Restore archive is not a regular file: ${archive_path}"
    [ ! -L "${archive_path}" ] || fail "Restore archive cannot be a symbolic link"
    timestamp="$(date +%Y%m%d_%H%M%S)"
    restore_root="$(mktemp -d "${PROJECT_DIR}/backups/.restore-${timestamp}.XXXXXX")"
    staged_root="${restore_root}/staged"
    snapshot="${PROJECT_DIR}/backups/pre_restore_${timestamp}"
    chmod 0700 "${restore_root}"
    cp -- "${archive_path}" "${restore_root}/input.tar.gz"
    chmod 0600 "${restore_root}/input.tar.gz"
    chown -R 10001:10001 "${restore_root}"

    info "Creating an online backup before restore"
    backup
    info "Validating and staging restore archive"
    compose run --rm --no-deps \
        -v "${restore_root}:/restore:rw" \
        maintenance restore-stage /restore/input.tar.gz /restore/staged

    local database_swapped=0 uploads_swapped=0 had_database=0
    restore_rollback() {
        local failed_status="$?"
        trap - ERR
        set +e
        info "Restore failed; rolling back the previous database and uploads"
        compose down >/dev/null 2>&1 || true
        if [ "${uploads_swapped}" -eq 1 ]; then
            if [ -d "${PROJECT_DIR}/uploads/images" ]; then
                mv "${PROJECT_DIR}/uploads/images" "${snapshot}/failed-restored-images"
            fi
            if [ -d "${snapshot}/live-images" ]; then
                mv "${snapshot}/live-images" "${PROJECT_DIR}/uploads/images"
            fi
        fi
        if [ "${database_swapped}" -eq 1 ]; then
            if [ -f "${PROJECT_DIR}/data/xianyu_data.db" ]; then
                cp -- "${PROJECT_DIR}/data/xianyu_data.db" "${snapshot}/failed-restored.db"
            fi
            if [ "${had_database}" -eq 1 ] && [ -f "${snapshot}/live-xianyu_data.db" ]; then
                cp -- "${snapshot}/live-xianyu_data.db" "${PROJECT_DIR}/data/.xianyu_data.db.rollback-${timestamp}"
                chown 10001:10001 "${PROJECT_DIR}/data/.xianyu_data.db.rollback-${timestamp}"
                chmod 0640 "${PROJECT_DIR}/data/.xianyu_data.db.rollback-${timestamp}"
                mv -f "${PROJECT_DIR}/data/.xianyu_data.db.rollback-${timestamp}" "${PROJECT_DIR}/data/xianyu_data.db"
            else
                rm -f "${PROJECT_DIR}/data/xianyu_data.db"
            fi
            local suffix
            for suffix in -wal -shm; do
                if [ -f "${snapshot}/live-xianyu_data.db${suffix}" ]; then
                    cp -- "${snapshot}/live-xianyu_data.db${suffix}" "${PROJECT_DIR}/data/xianyu_data.db${suffix}"
                fi
            done
        fi
        chown -R 10001:10001 "${PROJECT_DIR}/data" "${PROJECT_DIR}/uploads" "${snapshot}"
        compose up -d --no-build --remove-orphans
        health_check || info "Rollback data was restored, but service health still requires operator attention"
        fail "Restore failed with status ${failed_status}; the previous data was restored"
    }

    info "Stopping services for restore"
    compose down
    trap 'restore_rollback' ERR
    mkdir -p "${snapshot}/data" "${snapshot}/uploads"
    chmod 0700 "${snapshot}"
    cp -a "${PROJECT_DIR}/data/." "${snapshot}/data/"

    if [ -f "${PROJECT_DIR}/data/xianyu_data.db" ]; then
        had_database=1
        cp -- "${PROJECT_DIR}/data/xianyu_data.db" "${snapshot}/live-xianyu_data.db"
    fi
    local suffix
    for suffix in -wal -shm; do
        if [ -f "${PROJECT_DIR}/data/xianyu_data.db${suffix}" ]; then
            mv "${PROJECT_DIR}/data/xianyu_data.db${suffix}" "${snapshot}/live-xianyu_data.db${suffix}"
        fi
    done
    cp -- "${staged_root}/data/xianyu_data.db" "${PROJECT_DIR}/data/.xianyu_data.db.restore-${timestamp}"
    chown 10001:10001 "${PROJECT_DIR}/data/.xianyu_data.db.restore-${timestamp}"
    chmod 0640 "${PROJECT_DIR}/data/.xianyu_data.db.restore-${timestamp}"
    mv -f "${PROJECT_DIR}/data/.xianyu_data.db.restore-${timestamp}" "${PROJECT_DIR}/data/xianyu_data.db"
    database_swapped=1

    local new_uploads="${PROJECT_DIR}/uploads/.images.restore-${timestamp}"
    mkdir -p "${new_uploads}"
    if [ -d "${staged_root}/static/uploads/images" ]; then
        cp -a "${staged_root}/static/uploads/images/." "${new_uploads}/"
    fi
    chown -R 10001:10001 "${new_uploads}"
    find "${new_uploads}" -type d -exec chmod 0750 {} +
    find "${new_uploads}" -type f -exec chmod 0640 {} +
    mv "${PROJECT_DIR}/uploads/images" "${snapshot}/live-images"
    uploads_swapped=1
    mv "${new_uploads}" "${PROJECT_DIR}/uploads/images"

    chown -R 10001:10001 "${PROJECT_DIR}/data" "${PROJECT_DIR}/uploads" "${snapshot}"
    compose up -d --no-build --remove-orphans
    health_check
    trap - ERR
    find "${restore_root}" -depth -delete
    info "Restore completed; rollback snapshot retained at ${snapshot}"
}

status() {
    compose ps
    compose exec -T maintenance python /app/production_maintenance.py health
    compose exec -T maintenance python /app/production_maintenance.py check-disk
    docker system df
}

logs() {
    compose logs --tail=200 -f "${2:-xianyu-app}"
}

case "${1:-help}" in
    init) init_config ;;
    validate) check_host; validate_config ;;
    deploy) deploy ;;
    update) update ;;
    health) health_check ;;
    backup) backup ;;
    restore) restore "$@" ;;
    status) status ;;
    logs) logs "$@" ;;
    stop) compose down ;;
    restart) compose restart; health_check ;;
    *)
        cat <<'USAGE'
Usage: ./docker-deploy.sh COMMAND

  init                 Create .env from the Linux template
  validate             Validate host, secrets, and Compose configuration
  deploy               Load/pull prebuilt images and start production services
  update               Backup, load/pull updated images, and restart safely
  health               Verify bridge source access, app health, and free disk
  backup               Create and integrity-check an online SQLite backup
  restore <archive>     Safely restore a validated backup with automatic rollback
  status               Show service state, backup health, and Docker disk usage
  logs [service]        Follow the last 200 lines for one service
  restart              Restart services and run health checks
  stop                 Stop containers without deleting data
USAGE
        ;;
esac
