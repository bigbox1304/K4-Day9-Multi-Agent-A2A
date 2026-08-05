import os
import json
import glob
from datetime import datetime
from dotenv import load_dotenv
from src.llm_client import LLMClient
from src.agents import CoordinatorAgent

load_dotenv()

def main():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY not found in .env")
        return

    llm_client = LLMClient(api_key)
    coord = CoordinatorAgent('data', llm_client)
    coord.load_data()
    
    os.makedirs('output', exist_ok=True)
    os.makedirs('logging', exist_ok=True)
    
    input_files = glob.glob('input/**/*.json', recursive=True)
    if not input_files:
        input_files = glob.glob('input/*.json', recursive=True)
        
    trace_logs = []
    
    for fpath in sorted(input_files):
        fname = os.path.basename(fpath)
        print(f"Processing {fname}...")
            
        with open(fpath, 'r', encoding='utf-8') as f:
            case_data = json.load(f)
            
        case_trace = []
        try:
            out_json = coord.process_case(case_data, fname, case_trace)
            with open(f'output/{fname}', 'w', encoding='utf-8') as f:
                json.dump(out_json, f, indent=2, ensure_ascii=False)
            trace_logs.append({"case_id": case_data.get("case_id"), "trace": case_trace, "status": "success"})
            print(f"Successfully processed {fname}")
        except Exception as e:
            print(f"Error processing {fname}: {e}")
            trace_logs.append({"case_id": case_data.get("case_id"), "trace": case_trace, "status": "error", "error": str(e)})

    with open('logging/trace.jsonl', 'w', encoding='utf-8') as f:
        for t in trace_logs:
            f.write(json.dumps(t) + '\n')
            
    meta = {
        "model_name": "gpt-4o-mini",
        "parameter_size": "Unknown (OpenAI)",
        "framework": "openai + python/pandas",
        "runtime": datetime.now().isoformat()
    }
    with open('logging/metadata.json', 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2)
        
if __name__ == '__main__':
    main()
