#!/usr/bin/env python3
"""Read-only, host-only acceptance checks for the frozen stage qualification."""
import hashlib, json, math, pathlib, shlex, subprocess, unittest, wave
from host_stage_probe import ROOT, MODELS, BIN, distance, words

class HostStagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest=json.loads((ROOT/'stages.json').read_text())
        cls.runs=[json.loads(l) for l in (ROOT/'host-stage-evidence/runs-normalized.jsonl').read_text().splitlines()]
        cls.corpus=json.loads((MODELS/'ref/asr/transcripts.json').read_text())

    def test_evalset(self):
        items=self.corpus['items']
        self.assertEqual(len(items),12)
        self.assertEqual(len(set(i['id'] for i in items)),12)
        self.assertEqual(len(list((MODELS/'ref/asr').glob('*.wav'))),12)
        self.assertEqual(sorted(__import__('collections').Counter(i['voice'] for i in items).values()),[4,4,4])
        self.assertEqual([i['id'] for i in items if i['mid_sentence_pause_ms']],['asr11','asr12'])
        self.assertEqual([i['expected_end_of_turn'] for i in items[-2:]],[True,False])
        for it in items:
            p=MODELS/it['audio']
            self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),it['sha256'])
            with wave.open(str(p)) as w:
                self.assertEqual((w.getframerate(),w.getnchannels(),w.getsampwidth()),(16000,1,2))
                self.assertAlmostEqual(w.getnframes()/16000,it['duration_s'])

    def test_wer_algorithm(self):
        self.assertEqual(words("Don't, STOP!"),['dont','stop'])
        self.assertEqual(distance(words('a b c'),words('a b c')),0)
        self.assertEqual(distance(words('a b c'),words('a x c')),1)
        self.assertEqual(distance(words('a b c'),words('a c')),1)
        self.assertEqual(distance(words('a b c'),words('a x b c')),1)
        self.assertEqual(distance(words('five minutes'),words('5 minutes')),1)

    def test_all_recipes_executed_and_argv_exact(self):
        count=0
        for stage in ('vad','turn','asr'):
            for entry in self.manifest['stages'][stage]:
                for rec in [entry,*entry['alternate_recipes']]:
                    count+=1
                    rows=[r for r in self.runs if r['recipe']==rec['id']]
                    self.assertEqual(len(rows),rec['host_results']['n'])
                    for r in rows:
                        self.assertEqual(r['returncode'],0)
                        self.assertTrue(r['valid_output'])
                        cmd=shlex.split(r['command'])
                        self.assertEqual(cmd[:4],['nice','-n','10',str(BIN)])
                        self.assertEqual(cmd[cmd.index('--threads')+1],'4')
                        self.assertEqual(cmd[cmd.index('--backend')+1],'cpu')
                        self.assertGreaterEqual(r['final_result_ms'],r['load_done_ms'])
                        template=rec['cli_args_template']
                        inp=next(i['audio'] for i in self.corpus['items'] if i['id']==r['item'])
                        outflag='--text-out' if stage=='asr' else '--segments-out' if stage=='vad' else None
                        out=cmd[cmd.index(outflag)+1] if outflag else ''
                        expect=[x.replace('{M}',str(MODELS)).replace('{IN}',str(MODELS/inp)).replace('{OUT}',out) for x in template]
                        self.assertEqual(cmd[4:],expect)
                        self.assertTrue((ROOT/r['log']).exists())
        self.assertEqual(count,16)

    def test_asr_corpus_aggregation(self):
        for e in self.manifest['stages']['asr']:
            rs=[r for r in self.runs if r['recipe']==e['id']]
            self.assertEqual(len(rs),12)
            errors=sum(distance(words(r['reference']),words(r['hypothesis'])) for r in rs)
            refs=sum(len(words(r['reference'])) for r in rs)
            self.assertEqual(e['host_results']['word_errors'],errors)
            self.assertAlmostEqual(e['host_results']['wer'],errors/refs)
            rtf=sum(r['final_result_ms']-r['load_done_ms'] for r in rs)/1000/sum(r['audio_s'] for r in rs)
            self.assertAlmostEqual(e['host_results']['rtf'],rtf)

    def test_failed_and_unsupported_not_promoted(self):
        self.assertFalse(self.manifest['stages']['turn'][0]['host_results']['quality_pass'])
        self.assertEqual(self.manifest['stages']['llm'],[])
        self.assertTrue(all(x['ttft_ms'] is None and x['tok_s'] is None for x in self.manifest['unsupported_llm_candidates']))
        self.assertTrue(all(x['returncode']!=0 for x in self.manifest['llm_negative_probes']))
        self.assertEqual(len(self.manifest['failed_experiments']),10)
        for r in self.runs:
            t=r['timings'].get('session.wall_ms')
            if t and t['count']>1:
                self.assertIsNone(r['session_wall_ms'])
                self.assertIsNone(r['session_rtf'])

    def test_download_budget(self):
        d=self.manifest['downloads']
        self.assertLessEqual(d['new_package_payload_bytes'],4_000_000_000)
        self.assertEqual(d['new_package_payload_bytes'],sum(p['size_bytes'] for p in d['packages']))
        self.assertEqual(len(d['packages']),9)

    def test_referee_and_epoch1_evalset_unchanged(self):
        for rel in ('mobile/bench_s25.py','mobile/evalset/v1.json'):
            original=subprocess.check_output(['git','show','1139ca41ed752038fe1300950bf8918963403e29:'+rel],cwd=ROOT.parent)
            self.assertEqual((ROOT.parent/rel).read_bytes(),original)

if __name__=='__main__':unittest.main(verbosity=2)
