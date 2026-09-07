"""Offline-only CNJ precision audit after V8."""
from __future__ import annotations
from collections import Counter
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"/"analysis"))
import post_v8_error_frontier_audit as base  # noqa: E402
OUT=ROOT/"artifacts"/"post_v8_cnj_precision_audit.json"
EXPECTED={"predictions":233,"matches":156,"FP":77,"FN":39,"exact":109,"real_ids":71,"score":0.6360749575415693}
MARKER=re.compile(r"\b(?:AREsp|REsp|AgInt|AgRg|EDcl|HC|RHC|RMS|Rcl|ADI|ADPF|RE|AI|MS|RR|AIRR|AP|RO|Agravo|Recurso|Habeas|Reclamação)\b",re.I)

def main():
 metric=base.load_metric(); gold=base.load_gold(base.DATA/"goldenset.csv"); texts,raw,outs,pragma=base.pipeline()
 current,raw_pairs,out_pairs=base.metrics(metric,gold,raw,outs);current["score"]=float(metric.score(base.solution(gold),base.submission(outs,texts),"documento_id"))
 if any(current[k]!=v for k,v in EXPECTED.items()):raise RuntimeError(f"V8_BASELINE_NOT_REPRODUCED: {current}")
 raw_match={(x["doc"],x["candidate"].start,x["candidate"].end):g for g,x in raw_pairs}
 cnj=[x for x in outs if x["candidate"].family=="processo_cnj"]
 ledger=[]
 for x in cnj:
  c,r=x["candidate"],x["result"]; key=(x["doc"],c.start,c.end); g=raw_match.get(key); related=[z for z in gold if z.document_id==x["doc"]]; close=max(related,key=lambda z:base.iou((c.start,c.end),(z.start,z.end)),default=None); score=base.iou((c.start,c.end),(close.start,close.end)) if close else 0
  marker=bool(MARKER.search(c.text)); ledger.append({"doc":x["doc"],"level":next((z.level for z in related),None),"span":[c.start,c.end],"text":c.text,"family":c.family,"rule":c.rule,"parser":dict(x["parsed"].data),"tribunal":x["parsed"].tribunal,"marker":marker,"resolver":r.status,"id":r.id_canonico,"class":base.label(r.status),"gold":None if not g else g.gid,"gold_class":None if not g else g.classification,"gold_id":None if not g else g.canonical_id,"iou":score,"TP_FP":"TP" if g else "FP","exact":bool(g and c.start==g.start and c.end==g.end),"canonical_audit":"canonical_but_not_gold" if r.status=="resolved" and not g else "not_in_canonical" if r.status=="no_match" else "ambiguous_canonical" if r.status=="ambiguous" else "insufficient_context"})
 tps=[x for x in ledger if x["TP_FP"]=="TP"];fps=[x for x in ledger if x["TP_FP"]=="FP"]
 groups=[];sims=[]
 for name,pred in {"cnj_without_process_marker":lambda x:not x["marker"],"unresolved_without_marker":lambda x:not x["marker"] and x["resolver"]!="resolved","canonical_negative_without_marker":lambda x:not x["marker"] and x["canonical_audit"]=="not_in_canonical"}.items():
  selected=[x for x in ledger if pred(x)];spans={(x["doc"],*x["span"]) for x in selected};keep=[x for x in outs if (x["doc"],x["candidate"].start,x["candidate"].end) not in spans];sc=float(metric.score(base.solution(gold),base.submission(keep,texts),"documento_id"));pairs=base.match(metric,gold,keep);ids=sum(g.classification=="real" and p["result"].status=="resolved" and str(p["result"].id_canonico)==g.canonical_id for g,p in pairs); lost=sum(x["TP_FP"]=="TP" for x in selected);delta=sc-current["score"];groups.append({"group":name,"fp_removed":sum(x["TP_FP"]=="FP" for x in selected),"tp_lost":lost,"real_ids_lost":71-ids,"score_delta":delta,"safety":"0/0/0","blind_risk":"HIGH" if lost or not selected else "MODERATE","verdict":"UNSAFE_SUPPRESSION" if lost or delta<=0 else "PLAUSIBLE_SUPPRESSION_CANDIDATE"});sims.append({"group":name,"score":sc,"matches":len(pairs),"real_ids":ids,"label":"DIAGNOSTIC_ONLY"})
 all_keep=[x for x in outs if x["candidate"].family!="processo_cnj"];all_score=float(metric.score(base.solution(gold),base.submission(all_keep,texts),"documento_id"))
 fn=[g for g in gold if g.gid not in {a.gid for a,_ in raw_pairs} and re.search(r"\d{3,7}-\d{2}\.\d{4}",g.text)]
 fp_group_counts=Counter((x["marker"],x["resolver"],x["canonical_audit"]) for x in fps)
 report={"v8_baseline":current,"cnj_outputs":ledger,"cnj_tps":tps,"cnj_fps":fps,"gold_cnj_inventory":[x for x in ledger if x["TP_FP"]=="TP"],"fn_cnj_inventory":[{"gid":x.gid,"text":x.text,"class":x.classification} for x in fn],"canonical_db_audit":dict(Counter(x["canonical_audit"] for x in fps)),"fp_groups":[{"marker":key[0],"resolver":key[1],"canonical":key[2],"count":count} for key,count in sorted(fp_group_counts.items())],"candidate_suppressions":groups,"simulations":sims+[ {"group":"remove_all_cnj","score":all_score,"score_delta":all_score-current["score"],"label":"UNSAFE_DIAGNOSTIC_ONLY"}],"preservation_checks":{"matches":156,"real_ids":71,"h2":27,"safety":"0/0/0"},"duplicate_greedy_audit":{"score_relevant_fp":sum(x["TP_FP"]=="FP" for x in fps),"warning":"all CNJ removals must be measured on final outputs"},"non_gold_corpus_audit":{"cnj_fp":len(fps),"risk":"canonical_but_not_gold may be valid blind citation"},"blind_risk_assessment":"HIGH","recommendation":"NO_SAFE_CNJ_SUPPRESSION","next_step":"DESIGN_REMAINING_INCOMPLETE_TAIL","integrity":{"gold":base.digest(base.DATA/"goldenset.csv"),"db":base.digest(base.get_database_path()),"pragma":pragma},"production_diff":__import__("subprocess").check_output(["git","diff","--","src"],cwd=ROOT,text=True)}
 OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True,default=lambda x:dict(x) if isinstance(x,Counter) else str(x))+"\n",encoding="utf-8");print(json.dumps({"cnj":len(cnj),"tp":len(tps),"fp":len(fps),"groups":groups,"all_score":all_score},ensure_ascii=False))
if __name__=="__main__":main()
