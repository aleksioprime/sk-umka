#!/usr/bin/env bash
# Меню управления УМКОЙ с компьютера по SSH.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)" || exit 1
INSTALLER="$SCRIPT_DIR/../deploy/install.sh"
target=""
key=""
port=""
accent=""
reset=""
if [[ -t 1 && ${TERM:-dumb} != dumb && -z ${NO_COLOR:-} ]]; then
    accent=$'\033[1;36m'
    reset=$'\033[0m'
fi

usage() {
    cat <<'HELP'
Запуск: ./manager/menu.sh [пользователь@адрес] [--ssh-key ФАЙЛ] [--port ПОРТ]

Без адреса меню спросит, к какой плате подключиться.
Можно указать имя подключения из ~/.ssh/config.
HELP
}

valid_target() {
    [[ $1 != -* && $1 =~ ^([a-zA-Z0-9_.-]+@)?[a-zA-Z0-9_.:-]+$ ]]
}

valid_port() {
    [[ -z $1 ]] || { [[ $1 =~ ^[0-9]{1,5}$ ]] && (( 10#$1 >= 1 && 10#$1 <= 65535 )); }
}

expand_key() {
    case "$1" in
        "~/"*) printf '%s/%s' "$HOME" "${1:2}" ;;
        *) printf '%s' "$1" ;;
    esac
}

while (( $# )); do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --ssh-key|--port)
            if (( $# < 2 )) || [[ -z $2 ]]; then
                printf 'Укажите значение для %s.\n' "$1" >&2
                exit 2
            fi
            if [[ $1 == --ssh-key ]]; then key="$(expand_key "$2")"; else port="$2"; fi
            shift 2
            ;;
        *)
            if [[ -n $target ]] || ! valid_target "$1"; then
                usage >&2
                exit 2
            fi
            target="$1"
            shift
            ;;
    esac
done

if ! valid_port "$port" || [[ -n $key && ! -r $key ]]; then
    printf 'Проверьте порт SSH и путь к ключу.\n' >&2
    exit 2
fi
if ! command -v ssh >/dev/null; then
    printf 'На компьютере нужен SSH-клиент. Установите OpenSSH и запустите меню снова.\n' >&2
    exit 1
fi

select_device() {
    local candidate new_key new_port
    while true; do
        printf 'Адрес SSH (например umka@192.168.10.60, Enter — отмена): '
        IFS= read -r candidate && [[ -n $candidate ]] || return 1
        if valid_target "$candidate"; then break; fi
        printf 'Укажите логин и адрес через @ или имя подключения из ~/.ssh/config.\n' >&2
    done
    while true; do
        printf 'Путь к ключу SSH (Enter — по умолчанию): '
        IFS= read -r new_key || return 1
        new_key="$(expand_key "${new_key:-${1-}}")"
        if [[ -z $new_key || -r $new_key ]]; then break; fi
        printf 'Ключ не найден или недоступен для чтения.\n' >&2
    done
    while true; do
        printf 'Порт SSH (Enter — по умолчанию): '
        IFS= read -r new_port || return 1
        new_port="${new_port:-${2-}}"
        if valid_port "$new_port"; then break; fi
        printf 'Введите номер порта от 1 до 65535.\n' >&2
    done
    target="$candidate"
    key="$new_key"
    port="$new_port"
}

set_connection() {
    ssh_options=(-o ConnectTimeout=10)
    install_options=(--ssh-opt ConnectTimeout=10)
    if [[ -n $key ]]; then
        ssh_options+=(-i "$key")
        install_options+=(--ssh-key "$key")
    fi
    if [[ -n $port ]]; then
        ssh_options+=(-p "$port")
        install_options+=(--port "$port")
    fi
}

install_umka() {
    if [[ ! -x $INSTALLER ]]; then
        printf 'Не найден установщик deploy/install.sh. Запустите меню из полной копии проекта.\n' >&2
        return 1
    fi
    "$INSTALLER" "$target" "${install_options[@]}" "$@"
}

service_command() {
    ssh "${ssh_options[@]}" -t "$target" "sudo systemctl $1 umka.service" &&
        printf 'Команда выполнена. Состояние можно проверить в пункте 6.\n'
}

device_status() {
    ssh "${ssh_options[@]}" -n -T "$target" '
        printf "Плата: %s (%s)\n" "$(hostname)" "$(uname -m)"
        if [ -r /proc/device-tree/model ]; then
            printf "Модель: "
            tr -d "\000" < /proc/device-tree/model
            printf "\n"
        fi
        if [ -x "$HOME/app/.venv/bin/python" ]; then
            printf "Окружение УМКИ: "
            "$HOME/app/.venv/bin/python" --version
        else
            printf "Окружение УМКИ ещё не установлено.\n"
        fi
        loaded=$(systemctl show umka.service -p LoadState --value) || exit
        if [ "$loaded" = loaded ]; then
            printf "Состояние службы: "
            systemctl is-active umka.service || true
        else
            printf "Служба umka не настроена.\n"
        fi
        address=$(hostname -I 2>/dev/null | awk "{print \$1}")
        case "$address" in *:*) address="[$address]" ;; esac
        if [ -n "$address" ]; then
            printf "Экран в браузере: http://%s:3000/\n" "$address"
        fi
        if command -v curl >/dev/null; then
            if curl -fsS --max-time 3 http://127.0.0.1:3000/api/health >/dev/null 2>&1; then
                printf "Камера и модель готовы.\n"
            else
                printf "УМКА пока не подтвердила готовность. Проверьте журнал.\n"
            fi
        fi
    '
}

device_logs() {
    ssh "${ssh_options[@]}" -t "$target" '
        log="$HOME/app/data/logs/umka.log"
        if [ -s "$log" ]; then
            tail -n 40 "$log"
        else
            sudo journalctl -u umka.service --no-pager -n 40
        fi
    '
}

run_action() {
    printf '\n'
    if ! "$@"; then
        printf '\nНе удалось выполнить команду. Проверьте сообщение выше и попробуйте ещё раз.\n' >&2
    fi
}

menu() {
    printf '\n%s━━━ УМКА · управление по SSH ━━━%s\n' "$accent" "$reset"
    printf 'Устройство: %s\n\n' "$target"
    printf '  1  Установить УМКУ и окружение\n'
    printf '  2  Обновить файлы программы\n'
    printf '  3  Запустить УМКУ\n'
    printf '  4  Перезапустить УМКУ\n'
    printf '  5  Остановить УМКУ\n'
    printf '  6  Проверить плату и состояние\n'
    printf '  7  Посмотреть журнал\n'
    printf '  8  Выбрать другую плату\n'
    printf '  0  Выйти\n\n'
    printf 'Выберите действие: '
}

if [[ -z $target ]]; then
    select_device "$key" "$port" || exit 0
fi
set_connection
while true; do
    menu
    IFS= read -r choice || break
    case "$choice" in
        1) run_action install_umka --autostart ;;
        2) run_action install_umka --code-only ;;
        3) run_action service_command start ;;
        4) run_action service_command restart ;;
        5) run_action service_command stop ;;
        6) run_action device_status ;;
        7) run_action device_logs ;;
        8) if select_device; then set_connection; fi ;;
        0) break ;;
        *) printf 'Введите номер пункта меню.\n' ;;
    esac
done
