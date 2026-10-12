// Read-only browser-side regression: old JSON metadata cannot leak planners/designers into '클라이언트'.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'..','index.html'),'utf8');
const begin=html.indexOf('// Client-side revalidation');
const end=html.indexOf('function jobPostingRow(',begin);
assert.ok(begin>0&&end>begin,'title-first job filter exists');
const ctx=vm.createContext({});
new vm.Script(html.slice(begin,end)).runInContext(ctx);
const classify=(title,description='',area='클라이언트')=>
  vm.runInContext('visibleJobArea',ctx)({title,description,area});
const fixtures=[
 ['게임 PM (Unity 경험)',null], ['서비스 기획자 C++ 클라이언트 경험',null],
 ['UI 디자이너 언리얼 (Unreal)',null], ['게임 3D 아티스트 Unity',null],
 ['Product Manager / Unreal',null], ['Game Designer - Unity',null],
 ['일반 마케팅 신입',null],
 ['Unity C# 클라이언트 개발자','클라이언트'],
 ['게임 클라이언트 프로그래머','클라이언트'],
 ['게임 C++ 개발자','클라이언트'],
 ['Unreal 게임플레이 엔지니어','클라이언트'],
 ['서버 백엔드 개발자','백엔드/서버'],
 ['Java 서버 개발자 (Unity 협업)','백엔드/서버'],
 ['React 프론트엔드 개발자','프론트엔드'],
 ['AI 엔지니어','AI/데이터'],
 ['웹 서비스 소프트웨어 개발자','개발']
];
for(const [title,expected] of fixtures){
 const result=classify(title,'Unity Unreal C++ 프로젝트에서 기획, 디자이너와 협업');
 assert.equal(result,expected,title);
}
const jobs=[
 {title:'Unity 게임 기획자',area:'클라이언트',skills:['Unity']},
 {title:'Unreal 3D 디자이너',area:'클라이언트',skills:['Unreal']},
 {title:'게임 클라이언트 프로그래머',area:'개발',skills:['C++']},
 {title:'Java 백엔드 개발자',area:'클라이언트',skills:['Java']}
];
const filtered=vm.runInContext('developerJobPostings',ctx)(jobs);
assert.equal(filtered.length,2);
assert.equal(filtered[0].area,'클라이언트');
assert.equal(filtered[1].area,'백엔드/서버');
assert.equal(jobs[2].area,'개발','source cache should not be mutated');
assert.match(html,/const list=developerJobPostings\(cached\?\.jobPostings\|\|\[\]\)/);
assert.match(html,/const jobs=developerJobPostings\(d.jobPostings\|\|\[\]\)/);
console.log('PASS: non-developer PM/planning/design excluded from stale client tags and hiring counters');
