#!/usr/bin/env bash

set -euo pipefail

DB_PATH="${GAME_DB_PATH:-data/app.db}"
FLAG_PATH="${DEBUG_TOOLS_FLAG_PATH:-data/debug_tools_enabled.flag}"

usage() {
  cat <<'EOF'
Usage:
  scripts/game-debug.sh show
  scripts/game-debug.sh seed set [options]
  scripts/game-debug.sh seed clear

Seed set options:
  --enable | --disable
  --water N
  --water-earned N
  --water-spent N
  --fire N
  --fire-added N
  --fire-extinguished N
  --burn N
  --token-balance N
  --token-earned N
  --token-spent N
  --current-streak N
  --max-streak N
  --streak-group N
  --break-reason TEXT
  --reset-floors

Examples:
  scripts/game-debug.sh seed set --enable --water 3 --fire 11 --token-balance 2 --current-streak 4 --reset-floors
  scripts/game-debug.sh seed clear
  scripts/game-debug.sh show

Note:
  Mutating commands (`seed set`, `seed clear`) require debug tools ON:
    bash scripts/debug-tools.sh on
EOF
}

require_debug_tools_on() {
  if [[ -f "${FLAG_PATH}" ]]; then
    return 0
  fi

  cat >&2 <<EOF
Debug tools are OFF (${FLAG_PATH}).
Enable them first:
  bash scripts/debug-tools.sh on
EOF
  exit 1
}

ensure_db() {
  if [[ ! -f "${DB_PATH}" ]]; then
    echo "Database not found at ${DB_PATH}" >&2
    exit 1
  fi

  local has_table
  has_table=$(sqlite3 "${DB_PATH}" "select count(*) from sqlite_master where type='table' and name='game_debug_seed';")
  if [[ "${has_table}" == "0" ]]; then
    echo "game_debug_seed table is missing; applying latest migrations..."
    PYTHONPATH=apps/server/backend:apps/server/shared python -c "from app.migrations import ensure_schema_current; ensure_schema_current()"
  fi
}

sql_now_utc="strftime('%Y-%m-%d %H:%M:%f', 'now')"

show_state() {
  ensure_db
  echo "== game_debug_seed =="
  sqlite3 -header -column "${DB_PATH}" "select * from game_debug_seed;"
  echo
  echo "== game_correctness_state =="
  sqlite3 -header -column "${DB_PATH}" "select * from game_correctness_state;"
  echo
  echo "== game_streaks =="
  sqlite3 -header -column "${DB_PATH}" "select * from game_streaks;"
  echo
  echo "== game_tokens =="
  sqlite3 -header -column "${DB_PATH}" "select * from game_tokens;"
  echo
  echo "== pending incidents =="
  sqlite3 -header -column "${DB_PATH}" "select id, incident_type, severity, title, created_at from game_incidents where acknowledged_at is null order by created_at asc, id asc;"
}

ensure_seed_row() {
  sqlite3 "${DB_PATH}" "
  INSERT INTO game_debug_seed (
    id, enabled,
    water_units, water_earned_count, water_spent_count,
    fire_units, fire_added_count, fire_extinguished_count, burn_count,
    token_balance, token_earned_count, token_spent_count,
    current_streak, max_streak, active_streak_group_id, break_reason,
    correctness_event_floor_id, sync_floor_unix_ms, created_at, updated_at
  )
  VALUES (1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, NULL, 0, 0, ${sql_now_utc}, ${sql_now_utc})
  ON CONFLICT(id) DO NOTHING;
  "
}

apply_seed_to_live() {
  sqlite3 "${DB_PATH}" "
  INSERT INTO game_correctness_state (
    id, water_units, water_earned_count, water_spent_count, fire_units, fire_added_count,
    fire_extinguished_count, burn_count, last_burned_at, last_reconciled_at, updated_at
  )
  VALUES (
    1,
    (select water_units from game_debug_seed where id = 1),
    (select water_earned_count from game_debug_seed where id = 1),
    (select water_spent_count from game_debug_seed where id = 1),
    (select fire_units from game_debug_seed where id = 1),
    (select fire_added_count from game_debug_seed where id = 1),
    (select fire_extinguished_count from game_debug_seed where id = 1),
    (select burn_count from game_debug_seed where id = 1),
    NULL,
    (select last_reconciled_at from game_correctness_state where id = 1),
    ${sql_now_utc}
  )
  ON CONFLICT(id) DO UPDATE SET
    water_units = excluded.water_units,
    water_earned_count = excluded.water_earned_count,
    water_spent_count = excluded.water_spent_count,
    fire_units = excluded.fire_units,
    fire_added_count = excluded.fire_added_count,
    fire_extinguished_count = excluded.fire_extinguished_count,
    burn_count = excluded.burn_count,
    updated_at = excluded.updated_at;

  INSERT INTO game_tokens (id, balance, earned_count, spent_count, updated_at)
  VALUES (
    1,
    (select token_balance from game_debug_seed where id = 1),
    (select token_earned_count from game_debug_seed where id = 1),
    (select token_spent_count from game_debug_seed where id = 1),
    ${sql_now_utc}
  )
  ON CONFLICT(id) DO UPDATE SET
    balance = excluded.balance,
    earned_count = excluded.earned_count,
    spent_count = excluded.spent_count,
    updated_at = excluded.updated_at;

  INSERT INTO game_streaks (id, current_streak, max_streak, last_green_at, break_reason, active_streak_group_id, updated_at)
  VALUES (
    1,
    (select current_streak from game_debug_seed where id = 1),
    (select max_streak from game_debug_seed where id = 1),
    NULL,
    (select break_reason from game_debug_seed where id = 1),
    max((select active_streak_group_id from game_debug_seed where id = 1), 1),
    ${sql_now_utc}
  )
  ON CONFLICT(id) DO UPDATE SET
    current_streak = excluded.current_streak,
    max_streak = excluded.max_streak,
    break_reason = excluded.break_reason,
    active_streak_group_id = excluded.active_streak_group_id,
    updated_at = excluded.updated_at;
  "
}

seed_clear() {
  require_debug_tools_on
  ensure_db
  ensure_seed_row
  sqlite3 "${DB_PATH}" "
  UPDATE game_debug_seed
  SET
    enabled = 0,
    water_units = 0,
    water_earned_count = 0,
    water_spent_count = 0,
    fire_units = 0,
    fire_added_count = 0,
    fire_extinguished_count = 0,
    burn_count = 0,
    token_balance = 0,
    token_earned_count = 0,
    token_spent_count = 0,
    current_streak = 0,
    max_streak = 0,
    active_streak_group_id = 1,
    break_reason = NULL,
    correctness_event_floor_id = 0,
    sync_floor_unix_ms = 0,
    updated_at = ${sql_now_utc}
  WHERE id = 1;
  "
  apply_seed_to_live
  echo "Cleared debug seed and applied zeroed values to live game tables."
}

seed_set() {
  require_debug_tools_on
  ensure_db
  ensure_seed_row

  local set_enabled=""
  local set_water=""
  local set_water_earned=""
  local set_water_spent=""
  local set_fire=""
  local set_fire_added=""
  local set_fire_extinguished=""
  local set_burn=""
  local set_token_balance=""
  local set_token_earned=""
  local set_token_spent=""
  local set_current_streak=""
  local set_max_streak=""
  local set_streak_group=""
  local set_break_reason=""
  local reset_floors=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --enable)
        set_enabled=1
        shift
        ;;
      --disable)
        set_enabled=0
        shift
        ;;
      --water)
        set_water="$2"
        shift 2
        ;;
      --water-earned)
        set_water_earned="$2"
        shift 2
        ;;
      --water-spent)
        set_water_spent="$2"
        shift 2
        ;;
      --fire)
        set_fire="$2"
        shift 2
        ;;
      --fire-added)
        set_fire_added="$2"
        shift 2
        ;;
      --fire-extinguished)
        set_fire_extinguished="$2"
        shift 2
        ;;
      --burn)
        set_burn="$2"
        shift 2
        ;;
      --token-balance)
        set_token_balance="$2"
        shift 2
        ;;
      --token-earned)
        set_token_earned="$2"
        shift 2
        ;;
      --token-spent)
        set_token_spent="$2"
        shift 2
        ;;
      --current-streak)
        set_current_streak="$2"
        shift 2
        ;;
      --max-streak)
        set_max_streak="$2"
        shift 2
        ;;
      --streak-group)
        set_streak_group="$2"
        shift 2
        ;;
      --break-reason)
        set_break_reason="$2"
        shift 2
        ;;
      --reset-floors)
        reset_floors=1
        shift
        ;;
      *)
        echo "Unknown option: $1" >&2
        usage
        exit 1
        ;;
    esac
  done

  local updates=()
  [[ -n "${set_enabled}" ]] && updates+=("enabled = ${set_enabled}")
  [[ -n "${set_water}" ]] && updates+=("water_units = ${set_water}")
  [[ -n "${set_water_earned}" ]] && updates+=("water_earned_count = ${set_water_earned}")
  [[ -n "${set_water_spent}" ]] && updates+=("water_spent_count = ${set_water_spent}")
  [[ -n "${set_fire}" ]] && updates+=("fire_units = ${set_fire}")
  [[ -n "${set_fire_added}" ]] && updates+=("fire_added_count = ${set_fire_added}")
  [[ -n "${set_fire_extinguished}" ]] && updates+=("fire_extinguished_count = ${set_fire_extinguished}")
  [[ -n "${set_burn}" ]] && updates+=("burn_count = ${set_burn}")
  [[ -n "${set_token_balance}" ]] && updates+=("token_balance = ${set_token_balance}")
  [[ -n "${set_token_earned}" ]] && updates+=("token_earned_count = ${set_token_earned}")
  [[ -n "${set_token_spent}" ]] && updates+=("token_spent_count = ${set_token_spent}")
  [[ -n "${set_current_streak}" ]] && updates+=("current_streak = ${set_current_streak}")
  [[ -n "${set_max_streak}" ]] && updates+=("max_streak = ${set_max_streak}")
  [[ -n "${set_streak_group}" ]] && updates+=("active_streak_group_id = max(${set_streak_group}, 1)")

  if [[ -n "${set_break_reason}" ]]; then
    local escaped_reason
    escaped_reason=$(printf "%s" "${set_break_reason}" | sed "s/'/''/g")
    updates+=("break_reason = '${escaped_reason}'")
  fi

  if [[ ${reset_floors} -eq 1 ]]; then
    updates+=("correctness_event_floor_id = (select coalesce(max(id), 0) from game_events)")
    updates+=("sync_floor_unix_ms = (select coalesce(cast(strftime('%s', max(completed_at)) as integer) * 1000, 0) from ynab_sync where completed_at is not null and status in ('matched_updated','created'))")
  fi

  updates+=("updated_at = ${sql_now_utc}")
  local update_sql
  update_sql=$(IFS=", "; echo "${updates[*]}")

  sqlite3 "${DB_PATH}" "UPDATE game_debug_seed SET ${update_sql} WHERE id = 1;"
  apply_seed_to_live
  echo "Updated debug seed and applied values to live game tables."
}

main() {
  if [[ $# -lt 1 ]]; then
    usage
    exit 1
  fi

  case "$1" in
    show)
      show_state
      ;;
    seed)
      if [[ $# -lt 2 ]]; then
        usage
        exit 1
      fi
      case "$2" in
        set)
          shift 2
          seed_set "$@"
          ;;
        clear)
          seed_clear
          ;;
        *)
          usage
          exit 1
          ;;
      esac
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
