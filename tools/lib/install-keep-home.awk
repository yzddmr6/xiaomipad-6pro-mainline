# SPDX-License-Identifier: MIT
# BusyBox awk: validate account databases, select ordinary users, or merge.
BEGIN { FS=OFS=":" }
function fail(s) { print "liuqin-install: KEEP-HOME: " s; bad=1; exit 1 }
function ordinary(n) { return n >= 1000 && n <= 59999 }
function members(s, selected,    a,n,i,r) {
 n=split(s,a,","); r=""
 for(i=1;i<=n;i++) if(a[i]!="" && (!selected || a[i] in users)) r=r (r==""?"":",") a[i]
 return r
}
function append(a,b,    list,n,i) {
 n=split(b,list,","); for(i=1;i<=n;i++) if(list[i]!="" && index("," a ",","," list[i] ",")==0) a=a (a==""?"":",") list[i]
 return a
}
{
 file=FILENAME; sub(/^.*\//,"",file)
 side=(index(FILENAME,old "/")==1 ? "old" : "new")
 if ($1 !~ /^[a-zA-Z_][a-zA-Z0-9_.-]*\$?$/ || seen[side,file,$1]++) fail("invalid/duplicate name in " side " " file)
 if(file=="passwd") {
  if(NF!=7 || $3!~/^[0-9]+$/ || $4!~/^[0-9]+$/ || $6!~/^\// || $7!~/^\//) fail("malformed passwd")
  if(uid[side,$3+0]++) fail("duplicate UID")
  pname[side,$1]=1; pgid[side,$1]=$4+0
  if(side=="old" && ordinary($3)) { users[$1]=1; userids[$3+0]=1; lines[file,$1]=$0; order[++count]=$1 }
  if(side=="new" && ($1 in users || ($3+0) in userids)) fail("account name/UID conflict: " $1)
 } else if(file=="shadow") {
  if(NF!=9) fail("malformed shadow")
  for(i=3;i<=9;i++) if($i!="" && $i!~/^[0-9]+$/) fail("malformed shadow age")
  if(side=="new" && $1 in users) fail("shadow name conflict: " $1)
  shadow[side,$1]=1
  if(side=="old" && $1 in users) lines[file,$1]=$0
 } else if(file=="group") {
  if(NF!=4 || $3!~/^[0-9]+$/ || ($4!="" && $4!~/^[a-zA-Z0-9_.$,-]+$/)) fail("malformed group")
  if(gid[side,$3+0]++) fail("duplicate GID")
  gname[side,$1]=$3+0
  if(side=="old") {
   if(!ordinary($3)) $4=members($4,1)
   lines[file,$1]=$0; groupmembers[$1]=$4
   if(ordinary($3)) { groups[$1]=1; groupids[$3+0]=1; gorder[++gcount]=$1 }
  } else {
   if($1 in groups || ($3+0) in groupids) fail("group name/GID conflict: " $1)
   $4=append($4,groupmembers[$1])
  }
 } else if(file=="gshadow") {
  if(NF!=4) fail("malformed gshadow")
  gs[side,$1]=1
  if(side=="old") {
   if(!ordinary(gname[side,$1])) { $3=members($3,1); $4=members($4,1) }
   lines[file,$1]=$0; admins[$1]=$3
  } else { $3=append($3,admins[$1]); $4=append($4,groupmembers[$1]) }
 } else fail("unexpected account file")
 if(side=="new") print > (out "/" file)
}
END {
 if(bad) exit 1
 if(mode=="merge") {
  for(g in groups) if(gs["new",g]) fail("gshadow name conflict: " g)
 }
 for(i=1;i<=count;i++) {
  u=order[i]
  if(!shadow["old",u] || !gid["old",pgid["old",u]]) fail("missing shadow/primary group: " u)
  if(mode=="merge" && !gid["new",pgid["old",u]] && !(pgid["old",u] in groupids)) fail("missing destination primary group: " u)
  print lines["passwd",u] > (out "/passwd"); print lines["shadow",u] > (out "/shadow")
  if(mode=="snapshot") print u > (out "/users")
 }
 # Keep system group definitions only as membership inputs to the later merge.
 for(g in groupmembers) {
  if(!gs["old",g]) fail("missing gshadow: " g)
  if(mode=="snapshot" || g in groups) {
   print lines["group",g] > (out "/group"); print lines["gshadow",g] > (out "/gshadow")
  }
 }
}
