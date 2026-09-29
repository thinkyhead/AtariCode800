// Ad-hoc scope probe for the Atari BASIC grammar.
//
//   node tools/scopes.js '10 PRINT "A"' '20 A=1'   each line from INITIAL
//   node tools/scopes.js --doc FILE               whole file with rule state
//                                                 carried line to line, as
//                                                 VSCode and vscode-tmgrammar-test
//                                                 tokenize; flags lines that
//                                                 leave a region open (d>0)
const vt=require('vscode-textmate'), oni=require('vscode-oniguruma'), fs=require('fs');
const G=process.env.GRAMMAR||'syntax/ataribasic.tmLanguage.json';
oni.loadWASM(fs.readFileSync(require.resolve('vscode-oniguruma/release/onig.wasm')).buffer).then(async()=>{
  const reg=new vt.Registry({onigLib:Promise.resolve({createOnigScanner:s=>new oni.OnigScanner(s),createOnigString:s=>new oni.OnigString(s)}),
    loadGrammar:async sc=>sc==='source.ataribasic'?vt.parseRawGrammar(fs.readFileSync(G,'utf8'),G):null});
  const g=await reg.loadGrammar('source.ataribasic');
  const args=process.argv.slice(2);
  const doc=args[0]==='--doc';
  const lines=doc?fs.readFileSync(args[1],'utf8').split('\n'):args;
  const depth=s=>{let n=0;for(;s&&s.parent;s=s.parent)n++;return n;};
  let state=vt.INITIAL;
  lines.forEach((line,i)=>{
    const r=g.tokenizeLine(line, doc?state:vt.INITIAL);
    if(doc){
      const d=depth(r.ruleStack);
      if(d>0||process.env.ALL){
        // The scopes an empty probe line inherits = what is still open.
        const open=g.tokenizeLine('', r.ruleStack).tokens[0].scopes.slice(1).join(' ');
        console.log(String(i+1).padStart(4)+' d='+d+' '+JSON.stringify(line)+'\n       open: '+open);
        if(process.env.ALL) for(const t of r.tokens) console.log('        '+JSON.stringify(line.slice(t.startIndex,t.endIndex)).padEnd(12)+' '+t.scopes.slice(1).join(' '));
      }
      state=r.ruleStack;
      return;
    }
    console.log('\n--- '+JSON.stringify(line)+' ---');
    for(const t of r.tokens){
      const txt=line.slice(t.startIndex,t.endIndex);
      console.log('  '+JSON.stringify(txt).padEnd(12)+' '+t.scopes.slice(1).join(' '));
    }
  });
});
