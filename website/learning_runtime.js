function learningRuntimeCopy(value){
  if(!value||typeof value!=="object")return{};
  var out=Array.isArray(value)?[]:{};
  Object.keys(value).forEach(function(key){
    var item=value[key];
    out[key]=item&&typeof item==="object"?learningRuntimeCopy(item):item;
  });
  return out;
}
function learningRuntimePlainObject(value){
  if(!value||typeof value!=="object")return false;
  var prototype=Object.getPrototypeOf(value);
  return prototype===null||prototype===Object.prototype;
}
function learningRuntimeFinite(value){
  return typeof value==="number"&&isFinite(value);
}
function learningRuntimeValues(state,key,defaultFactory,direct){
  var defaults=typeof defaultFactory==="function"&&learningRuntimePlainObject(defaultFactory())?defaultFactory():{};
  var source=learningRuntimePlainObject(state&&state[key])?state[key]:(direct&&learningRuntimePlainObject(state)?state:{}),out={};
  Object.keys(defaults).forEach(function(name){
    var value=learningRuntimeFinite(source[name])?source[name]:defaults[name];
    if(learningRuntimeFinite(value))out[name]=value;
  });
  return out;
}
function learningRuntimeLeague(){
  return typeof D!=="undefined"&&D&&typeof D.league==="string"?D.league:"";
}
function learningModelState(league){
  var models=typeof EMBEDDED_MODELS!=="undefined"&&learningRuntimePlainObject(EMBEDDED_MODELS)?EMBEDDED_MODELS:{};
  var state=models[league];
  return learningRuntimePlainObject(state)?learningRuntimeCopy(state):{};
}
function activeWeights(){
  var state=learningModelState(learningRuntimeLeague());
  return learningRuntimeValues(state,"factors",defaultWeights,true);
}
function activeCalibration(){
  var state=learningModelState(learningRuntimeLeague());
  return learningRuntimeValues(state,"calibration",defaultCalibration,false);
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
