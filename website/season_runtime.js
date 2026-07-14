(function(root){
  'use strict';
  function validCatalog(c){return !!(c&&c.data&&c.items&&c.items.length)}
  function catalogFor(league,pl,ll){
    if(league==='pl'&&validCatalog(pl))return pl;
    if(league==='laliga'&&validCatalog(ll))return ll;
    return null;
  }
  function resolveKey(catalog,requested){
    if(!validCatalog(catalog))return '';
    if(requested&&catalog.data[requested])return requested;
    if(catalog.current&&catalog.data[catalog.current])return catalog.current;
    return catalog.items[0]&&catalog.data[catalog.items[0].key]?catalog.items[0].key:'';
  }
  function packFor(catalog,requested){
    var key=resolveKey(catalog,requested);
    return key?catalog.data[key]:null;
  }
  function guessKey(league,season){
    if(league==='laliga')return 'llg_'+season;
    if(league==='pl')return 'plg_'+season;
    return 'wcg';
  }
  function migrateLegacyLaligaGuesses(storage,season){
    if(season!=='2025-26')return false;
    var target=guessKey('laliga',season);
    if(storage.getItem(target)!==null)return false;
    var raw=storage.getItem('llg');
    if(raw===null)return false;
    try{
      var parsed=JSON.parse(raw);
      if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))return false;
      storage.setItem(target,JSON.stringify(parsed));
      return true;
    }catch(e){return false}
  }
  root.SEASON_RUNTIME={catalogFor:catalogFor,resolveKey:resolveKey,packFor:packFor,guessKey:guessKey,migrateLegacyLaligaGuesses:migrateLegacyLaligaGuesses};
})(typeof globalThis!=='undefined'?globalThis:this);
