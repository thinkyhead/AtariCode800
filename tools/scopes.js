const vt=require('vscode-textmate'), oni=require('vscode-oniguruma'), fs=require('fs');
const G='syntax/ataribasic.tmLanguage.json';
oni.loadWASM(fs.readFileSync(require.resolve('vscode-oniguruma/release/onig.wasm')).buffer).then(async()=>{
  const reg=new vt.Registry({onigLib:Promise.resolve({createOnigScanner:s=>new oni.OnigScanner(s),createOnigString:s=>new oni.OnigString(s)}),
    loadGrammar:async sc=>sc==='source.ataribasic'?vt.parseRawGrammar(fs.readFileSync(G,'utf8'),G):null});
  const g=await reg.loadGrammar('source.ataribasic');
  for(const line of process.argv.slice(2)){
    console.log('\n--- '+JSON.stringify(line)+' ---');
    for(const t of g.tokenizeLine(line, vt.INITIAL).tokens){
      const txt=line.slice(t.startIndex,t.endIndex);
      console.log('  '+JSON.stringify(txt).padEnd(12)+' '+t.scopes.slice(1).join(' '));
    }
  }
});
