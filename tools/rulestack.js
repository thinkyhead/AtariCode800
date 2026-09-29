// Show the rule stack left open after tokenizing each given line.
//   node tools/rulestack.js '10 A=1'
const vt=require('vscode-textmate'), oni=require('vscode-oniguruma'), fs=require('fs');
const G=process.env.GRAMMAR||'syntax/ataribasic.tmLanguage.json';
oni.loadWASM(fs.readFileSync(require.resolve('vscode-oniguruma/release/onig.wasm')).buffer).then(async()=>{
  const reg=new vt.Registry({onigLib:Promise.resolve({createOnigScanner:s=>new oni.OnigScanner(s),createOnigString:s=>new oni.OnigString(s)}),
    loadGrammar:async sc=>sc==='source.ataribasic'?vt.parseRawGrammar(fs.readFileSync(G,'utf8'),G):null});
  const g=await reg.loadGrammar('source.ataribasic');
  for(const line of process.argv.slice(2)){
    const r=g.tokenizeLine(line, vt.INITIAL);
    console.log('--- '+JSON.stringify(line));
    for(let s=r.ruleStack;s;s=s.parent){
      const rule=(g._ruleId2desc||g._grammar?._ruleId2desc||[])[s.ruleId];
      const desc=rule?JSON.stringify({begin:rule._begin&&rule._begin.source,end:rule._end&&rule._end.source,name:rule._name,contentName:rule._contentName}).slice(0,400):'(rule '+s.ruleId+')';
      console.log('  endRule='+JSON.stringify(s.endRule)+' '+desc);
    }
  }
});
