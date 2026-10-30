# weather-skills-core moved from rhiza-research to the weather-skills org, and
# the forecasting-skills old-dev skills still pin the old (now gone) URL in their
# inline script metadata. skills-runner starts each skill with `uv run --script`,
# which has no overrides, so redirect the URL in git, which uv uses for the fetch.
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0="url.https://github.com/weather-skills/weather-skills-core.insteadOf"
export GIT_CONFIG_VALUE_0="https://github.com/rhiza-research/weather-skills-core"
