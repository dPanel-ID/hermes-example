#!/bin/bash

export CURRENT_DIR=$(pwd)
export HERMES_HOME=${CURRENT_DIR}/.hermes


curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-setup --non-interactive --hermes-home ${HERMES_HOME}
