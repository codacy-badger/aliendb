#!/bin/sh

if [ ! -d "static" ]; then
        exit 1
fi

ROOT=$(pwd)

cd $ROOT/static/css
cat bootstrap.css \
  custom.css \
  | python -m rcssmin > main.min.css

cd $ROOT/static/js
cat custom.js \
  highcharts.js \
  jquery.min.js \
  moment.min.js \
  tether.min.js \
  bootstrap.min.js \
  | python -m rjsmin > main.min.js
