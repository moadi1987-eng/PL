(function(root){
  'use strict';
  var MAPS={
    r32ToR16:[[0,3],[2,5],[1,4],[6,7],[11,10],[9,8],[14,13],[12,15]],
    r16ToQf:[[1,0],[4,5],[2,3],[6,7]],
    qfToSf:[[0,1],[2,3]]
  };
  var SIDE_ORDER={
    left:{r32:[2,5,0,3,11,10,9,8],r16:[1,0,4,5],qf:[0,1],sf:[0]},
    right:{r32:[1,4,6,7,14,13,12,15],r16:[2,3,6,7],qf:[2,3],sf:[1]}
  };
  var ROUND_WORDS={'round of 32':'r32','round of 16':'r16','quarterfinal':'qf','semifinal':'sf'};

  function fixtureSort(a,b){
    var ak=new Date(a&&a.ko||'').getTime(),bk=new Date(b&&b.ko||'').getTime();
    if(!isFinite(ak))ak=Number.MAX_SAFE_INTEGER;
    if(!isFinite(bk))bk=Number.MAX_SAFE_INTEGER;
    return ak-bk||(+a.id||0)-(+b.id||0);
  }
  function teamFor(teams,id){return teams&&(teams[id]||teams[String(id)])||{}}
  function ref(round,index,match){return{key:round+'-'+index,round:round,index:index,match:match}}
  function refs(rounds,round,indexes){return indexes.map(function(index){return ref(round,index,rounds[round][index])})}
  function participant(match,id){return!!(match&&(+match.h===+id||+match.a===+id))}
  function placeholderMeta(team){
    var m=String(team&&team.n||'').match(/^(Round of 32|Round of 16|Quarterfinal|Semifinal)\s+(\d+)\s+(Winner|Loser)$/i);
    if(!m)return null;
    return{round:ROUND_WORDS[m[1].toLowerCase()],index:+m[2]-1,type:m[3].toLowerCase()};
  }
  function partition(fixtures){
    var knockout=(fixtures||[]).filter(function(match){return match&&!match.grp}).slice().sort(fixtureSort);
    if(knockout.length!==32)return{ready:false,reason:'expected-32-knockout-fixtures',count:knockout.length};
    var ids={};
    for(var i=0;i<knockout.length;i++){
      var id=String(knockout[i].id);
      if(ids[id])return{ready:false,reason:'duplicate-knockout-fixture-id',count:knockout.length};
      ids[id]=true;
    }
    return{ready:true,count:32,r32:knockout.slice(0,16),r16:knockout.slice(16,24),qf:knockout.slice(24,28),sf:knockout.slice(28,30),third:knockout[30],final:knockout[31]};
  }
  function link(fromRound,fromIndex,toRound,toIndex,type){
    return{fromRound:fromRound,fromIndex:fromIndex,fromKey:fromRound+'-'+fromIndex,toRound:toRound,toIndex:toIndex,toKey:toRound+'-'+toIndex,type:type||'winner'};
  }
  function mappedLinks(){
    var out=[];
    function add(fromRound,toRound,pairs){pairs.forEach(function(pair,toIndex){pair.forEach(function(fromIndex){out.push(link(fromRound,fromIndex,toRound,toIndex,'winner'))})})}
    add('r32','r16',MAPS.r32ToR16);
    add('r16','qf',MAPS.r16ToQf);
    add('qf','sf',MAPS.qfToSf);
    out.push(link('sf',0,'final',0,'winner'),link('sf',1,'final',0,'winner'));
    out.push(link('sf',0,'third',0,'loser'),link('sf',1,'third',0,'loser'));
    return out;
  }
  function matchFor(rounds,round,index){
    if(round==='final'||round==='third')return rounds[round];
    return rounds[round]&&rounds[round][index];
  }
  function resolveLink(raw,rounds,teams){
    var source=matchFor(rounds,raw.fromRound,raw.fromIndex),destination=matchFor(rounds,raw.toRound,raw.toIndex);
    var ids=destination?[destination.h,destination.a]:[],teamId=null,placeholder=false;
    ids.forEach(function(id){
      if(teamId!=null)return;
      if(participant(source,id)){teamId=+id;return}
      var meta=placeholderMeta(teamFor(teams,id));
      if(meta&&meta.round===raw.fromRound&&meta.index===raw.fromIndex&&meta.type===raw.type)placeholder=true;
    });
    return{fromKey:raw.fromKey,toKey:raw.toKey,type:raw.type,confirmed:teamId!=null,teamId:teamId,placeholder:placeholder};
  }
  function build(fixtures,teams){
    var rounds=partition(fixtures);
    if(!rounds.ready)return rounds;
    var links=mappedLinks().map(function(item){return resolveLink(item,rounds,teams)});
    return{
      ready:true,count:32,rounds:rounds,links:links,
      sides:{
        left:{r32:refs(rounds,'r32',SIDE_ORDER.left.r32),r16:refs(rounds,'r16',SIDE_ORDER.left.r16),qf:refs(rounds,'qf',SIDE_ORDER.left.qf),sf:refs(rounds,'sf',SIDE_ORDER.left.sf)},
        right:{sf:refs(rounds,'sf',SIDE_ORDER.right.sf),qf:refs(rounds,'qf',SIDE_ORDER.right.qf),r16:refs(rounds,'r16',SIDE_ORDER.right.r16),r32:refs(rounds,'r32',SIDE_ORDER.right.r32)}
      },
      final:ref('final',0,rounds.final),third:ref('third',0,rounds.third)
    };
  }
  root.WC_BRACKET_RUNTIME={build:build,partition:partition,maps:MAPS};
})(typeof globalThis!=='undefined'?globalThis:this);
