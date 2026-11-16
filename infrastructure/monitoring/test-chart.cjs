const assert=require('node:assert/strict');
const {chartModel,parseLines}=require('./dashboard.js');
const model=chartModel([{observed:1000},{observed:1010},{observed:1100}]);
assert.equal(model.position(1010000),0.1,'Time gaps must have proportional width');
assert.equal(model.position(1000000),0);assert.equal(model.position(1100000),1);
assert.equal(chartModel([{received:500}]).position(500000),0.5);
assert.equal(chartModel([{received:'bad'},{}]).points.length,0);
assert.deepEqual(parseLines('{"State":"running"}\nmalformed\n'),[{State:'running'}]);
console.log('CHART_OK: actual timestamps, gaps, single point and invalid telemetry');
