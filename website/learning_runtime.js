function learningRuntimeCopy(value){
  if(!value||typeof value!=="object")return{};
  var out=Array.isArray(value)?[]:{};
  Object.keys(value).forEach(function(key){
    var item=value[key];
    out[key]=item&&typeof item==="object"?learningRuntimeCopy(item):item;
  });
  return out;
}
function learningRuntimeLeague(){
  return typeof D!=="undefined"&&D&&typeof D.league==="string"?D.league:"";
}
function learningModelState(league){
  var models=typeof EMBEDDED_MODELS!=="undefined"&&EMBEDDED_MODELS&&typeof EMBEDDED_MODELS==="object"?EMBEDDED_MODELS:{};
  var state=models[league];
  return state&&typeof state==="object"?learningRuntimeCopy(state):{};
}
function activeWeights(){
  var state=learningModelState(learningRuntimeLeague());
  if(state.factors&&typeof state.factors==="object")return learningRuntimeCopy(state.factors);
  var defaults=typeof defaultWeights==="function"?defaultWeights():{},direct={};
  Object.keys(defaults).forEach(function(key){if(state[key]!=null)direct[key]=state[key];});
  if(Object.keys(direct).length)return learningRuntimeCopy(direct);
  return Object.keys(state).length?{}:learningRuntimeCopy(defaults);
}
function activeCalibration(){
  var state=learningModelState(learningRuntimeLeague());
  if(state.calibration&&typeof state.calibration==="object")return learningRuntimeCopy(state.calibration);
  return Object.keys(state).length?{}:(typeof defaultCalibration==="function"?learningRuntimeCopy(defaultCalibration()):{});
}
function scoreModelChoice(){
  var league=learningRuntimeLeague(),state=learningModelState(league);
  var history=typeof LEARNING_HISTORY!=="undefined"&&LEARNING_HISTORY&&typeof LEARNING_HISTORY==="object"?LEARNING_HISTORY:{};
  var status=history[league]&&history[league].model_status&&typeof history[league].model_status==="object"?history[league].model_status:{};
  var strategy=status.active_strategy||state.active_strategy||"baseline";
  return {
    strategy:strategy,
    candidateStrategy:status.candidate_strategy||state.candidate_strategy||(strategy==="v4"?"baseline":"v4"),
    status:status.status||"collecting",
    useV4:strategy==="v4",
    reason:status.reason||("active "+strategy),
  };
}
