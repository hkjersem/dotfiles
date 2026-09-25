#!/usr/bin/env bash
# Shared generated artifacts.

CLEAN_DIR_TARGETS=(
    node_modules dist .dist build .build out .out coverage .coverage
    .next .nuxt .output .turbo .cache .parcel-cache .vite .svelte-kit
    .docusaurus .vinext storybook-static
    test-results playwright-report blob-report .nyc_output
    __pycache__ .venv venv
)

CLEAN_FILE_TARGETS=(
    "*.tsbuildinfo"
    .eslintcache
    .stylelintcache
    .oxlintcache
)

CLEAN_DIR_TARGETS_REGEX=""
for clean_target in "${CLEAN_DIR_TARGETS[@]}"; do
    clean_target="${clean_target//./\\.}"
    if [[ -n "$CLEAN_DIR_TARGETS_REGEX" ]]; then
        CLEAN_DIR_TARGETS_REGEX+="|"
    fi
    CLEAN_DIR_TARGETS_REGEX+="$clean_target"
done

CLEAN_FILE_TARGETS_REGEX=""
for clean_target in "${CLEAN_FILE_TARGETS[@]}"; do
    clean_target="${clean_target//./\\.}"
    clean_target="${clean_target//\*/.*}"
    if [[ -n "$CLEAN_FILE_TARGETS_REGEX" ]]; then
        CLEAN_FILE_TARGETS_REGEX+="|"
    fi
    CLEAN_FILE_TARGETS_REGEX+="$clean_target"
done
