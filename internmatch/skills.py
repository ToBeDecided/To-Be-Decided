"""Skill taxonomy, skill extraction, and role "signatures" used for matching.

Everything here is plain data plus a few regex helpers, so the matcher stays
deterministic and runs offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Skill:
    name: str
    group: str
    aliases: tuple[str, ...] = ()
    # Short/ambiguous names ("C", "R", "Go") are only matched case-sensitively.
    case_sensitive: tuple[str, ...] = ()


def _s(name: str, group: str, *aliases: str, cs: tuple[str, ...] = (), name_alias: bool = True) -> Skill:
    # Case-sensitive skills and ambiguous names ("Testing", "Assembly") only match via their aliases.
    names = ((name,) if name_alias and not cs else ()) + aliases
    return Skill(name, group, tuple(a.lower() for a in names), cs)


SKILLS: tuple[Skill, ...] = (
    # Languages
    _s("Python", "language"),
    _s("Java", "language"),
    _s("JavaScript", "language", "js", "es6", "ecmascript"),
    _s("TypeScript", "language"),
    _s("C++", "language", "cpp"),
    _s("C", "language", cs=("C",)),
    _s("C#", "language", "csharp"),
    _s("Go", "language", "golang", cs=("Go",)),
    _s("Rust", "language"),
    _s("Kotlin", "language"),
    _s("Swift", "language", cs=("Swift",)),
    _s("Objective-C", "language", "objective c"),
    _s("Ruby", "language"),
    _s("PHP", "language"),
    _s("Scala", "language"),
    _s("R", "language", "rstudio", cs=("R",)),
    _s("MATLAB", "language"),
    _s("Julia", "language"),
    _s("Haskell", "language"),
    _s("OCaml", "language"),
    _s("Bash", "language", "shell scripting", "shell", "zsh"),
    _s("SQL", "language", "t-sql", "pl/sql", "nosql"),
    _s("HTML", "language", "html5"),
    _s("CSS", "language", "css3", "scss", "sass"),
    _s("Dart", "language"),
    _s("Assembly", "language", "assembly language", "x86", "x86 assembly", "arm assembly", "mips", name_alias=False),
    _s("Verilog", "hardware"),
    _s("SystemVerilog", "hardware", "system verilog", "uvm"),
    _s("VHDL", "hardware"),
    _s("Solidity", "language"),
    # Web / backend
    _s("React", "web", "react.js", "reactjs", cs=("React",)),
    _s("Next.js", "web", "nextjs"),
    _s("Vue", "web", "vue.js", "vuejs"),
    _s("Angular", "web", "angularjs"),
    _s("Svelte", "web"),
    _s("Redux", "web"),
    _s("Tailwind", "web", "tailwindcss"),
    _s("Node.js", "backend", "node", "nodejs"),
    _s("Express", "backend", "express.js", "expressjs", cs=("Express",)),
    _s("Django", "backend"),
    _s("Flask", "backend"),
    _s("FastAPI", "backend"),
    _s("Spring", "backend", "spring boot", "springboot", "spring framework", "spring mvc", name_alias=False),
    _s("Rails", "backend", "ruby on rails"),
    _s(".NET", "backend", "asp.net", "dotnet"),
    _s("GraphQL", "backend"),
    _s("REST APIs", "backend", "rest api", "restful", "rest apis", "restful api"),
    _s("gRPC", "backend"),
    _s("Microservices", "backend", "microservice"),
    _s("Distributed Systems", "backend", "distributed system", "distributed computing"),
    # Mobile
    _s("iOS", "mobile"),
    _s("Android", "mobile"),
    _s("React Native", "mobile"),
    _s("Flutter", "mobile"),
    _s("SwiftUI", "mobile"),
    # Data
    _s("PostgreSQL", "data", "postgres"),
    _s("MySQL", "data"),
    _s("MongoDB", "data", "mongo"),
    _s("Redis", "data"),
    _s("SQLite", "data"),
    _s("DynamoDB", "data"),
    _s("Elasticsearch", "data", "elastic search", "opensearch"),
    _s("Kafka", "data", "apache kafka"),
    _s("Spark", "data", "apache spark", "pyspark"),
    _s("Hadoop", "data"),
    _s("Airflow", "data", "apache airflow"),
    _s("dbt", "data"),
    _s("Snowflake", "data"),
    _s("BigQuery", "data", "big query"),
    _s("Pandas", "data"),
    _s("NumPy", "data"),
    _s("SciPy", "data"),
    _s("Matplotlib", "data", "seaborn", "plotly"),
    _s("Tableau", "data"),
    _s("Power BI", "data", "powerbi"),
    _s("Excel", "data", "microsoft excel", "ms excel", "vba", "spreadsheets", cs=("Excel",)),
    _s("ETL", "data", "data pipeline", "data pipelines"),
    _s("Data Analysis", "data", "data analytics", "data analyst", "exploratory data analysis"),
    # ML / AI
    _s("Machine Learning", "ml", "ml"),
    _s("Deep Learning", "ml", "neural network", "neural networks", "cnn", "rnn", "lstm"),
    _s("PyTorch", "ml", "torch"),
    _s("TensorFlow", "ml"),
    _s("Keras", "ml"),
    _s("scikit-learn", "ml", "sklearn", "scikit learn"),
    _s("XGBoost", "ml", "lightgbm"),
    _s("NLP", "ml", "natural language processing"),
    _s("Computer Vision", "ml", "opencv", "image processing"),
    _s("LLMs", "ml", "llm", "large language model", "large language models", "transformers",
       "hugging face", "huggingface", "langchain", "rag", "generative ai", "genai", "prompt engineering"),
    _s("Reinforcement Learning", "ml"),
    _s("MLOps", "ml", "mlflow", "kubeflow"),
    _s("CUDA", "ml", "gpu programming"),
    _s("A/B Testing", "data", "ab testing", "a/b tests", "experimentation"),
    # Cloud / DevOps
    _s("AWS", "cloud", "amazon web services", "ec2", "s3", "aws lambda"),
    _s("GCP", "cloud", "google cloud", "google cloud platform"),
    _s("Azure", "cloud", "microsoft azure"),
    _s("Docker", "cloud", "containers", "containerization"),
    _s("Kubernetes", "cloud", "k8s"),
    _s("Terraform", "cloud", "infrastructure as code"),
    _s("CI/CD", "cloud", "github actions", "jenkins", "circleci", "continuous integration"),
    _s("Linux", "cloud", "unix"),
    _s("Git", "tools", "github", "gitlab", "version control"),
    _s("Networking", "cloud", "tcp/ip", "tcp", "computer networks", "computer networking", "network programming",
       "socket programming", "network protocols", name_alias=False),
    _s("Operating Systems", "systems", "operating system", "kernel", "os development"),
    _s("Compilers", "systems", "compiler", "llvm"),
    _s("Algorithms", "cs", "data structures", "algorithm design", "data structures and algorithms"),
    _s("Object-Oriented Programming", "cs", "oop", "object oriented", "object-oriented"),
    _s("Testing", "cs", "unit testing", "unit tests", "integration testing", "pytest", "junit", "jest", "cypress",
       "test automation", "selenium", "tdd", name_alias=False),
    # Hardware / embedded
    _s("FPGA", "hardware", "xilinx", "vivado", "quartus"),
    _s("ASIC", "hardware"),
    _s("PCB Design", "hardware", "pcb", "altium", "kicad", "eagle", "pcb layout"),
    _s("Circuit Design", "hardware", "analog", "circuit analysis", "spice", "ltspice", "cadence",
       "virtuoso", "mixed-signal", "circuits"),
    _s("Embedded Systems", "hardware", "embedded", "microcontroller", "microcontrollers", "arduino",
       "raspberry pi", "stm32", "rtos", "freertos", "esp32", "bare metal"),
    _s("Firmware", "hardware"),
    _s("Signal Processing", "hardware", "dsp", "digital signal processing"),
    _s("Oscilloscope", "hardware", "multimeter", "logic analyzer", "lab equipment"),
    _s("Computer Architecture", "hardware", "computer organization", "risc-v", "riscv", "cache coherence"),
    _s("RF", "hardware", "rf design", "antenna", "antennas", "microwave"),
    _s("Power Electronics", "hardware", "power systems"),
    _s("CAD", "hardware", "solidworks", "autocad", "fusion 360", "creo", "catia", "onshape"),
    _s("Controls", "hardware", "control systems", "control theory", "simulink", "pid", "feedback control",
       name_alias=False),
    _s("ROS", "hardware", "ros2"),
    _s("Robotics", "hardware", "robot", "robots"),
    _s("Digital Design", "hardware", "digital logic", "rtl", "logic design", "timing analysis"),
    # Quant / math
    _s("Probability", "quant", "stochastic processes"),
    _s("Statistics", "quant", "statistical", "regression", "hypothesis testing", "bayesian"),
    _s("Linear Algebra", "quant"),
    _s("Stochastic Calculus", "quant"),
    _s("Time Series", "quant", "time-series", "forecasting"),
    _s("Optimization", "quant", "convex optimization", "linear programming", "integer programming",
       "numerical optimization", "operations research", name_alias=False),
    _s("Econometrics", "quant"),
    _s("Finance", "quant", "trading", "derivatives", "options pricing", "portfolio management", "fixed income",
       "financial modeling", "investing", "investment banking"),
    _s("Competitive Programming", "quant", "codeforces", "icpc", "usaco", "putnam", "leetcode",
       "topcoder", "atcoder", "math olympiad", "imo", "aime"),
    # Product / design / business
    _s("Product Management", "product", "product manager", "product roadmap", "roadmap", "prd",
       "product requirements", "go-to-market"),
    _s("User Research", "product", "usability testing", "user interviews", "customer discovery"),
    _s("Figma", "product", "sketch", "adobe xd"),
    _s("UX/UI Design", "product", "ux", "ui/ux", "user experience", "wireframing", "prototyping"),
    _s("Agile", "product", "scrum", "jira", "kanban"),
    _s("Product Analytics", "product", "google analytics", "mixpanel", "amplitude", "kpi", "kpis",
       "product metrics"),
    _s("Market Research", "product", "competitive analysis"),
    # Security
    _s("Cybersecurity", "security", "information security", "infosec", "network security", "application security",
       "security engineering", "cyber security"),
    _s("Penetration Testing", "security", "pentesting", "ctf", "capture the flag", "burp suite"),
    _s("Cryptography", "security"),
    _s("Reverse Engineering", "security", "ghidra", "ida pro", "binary exploitation"),
    # Other
    _s("Game Development", "other", "unity", "unreal", "unreal engine", "godot"),
    _s("Blockchain", "other", "web3", "ethereum", "smart contracts"),
)

SKILL_BY_NAME: dict[str, Skill] = {s.name.lower(): s for s in SKILLS}


def canonical(name: str) -> str | None:
    """Map a user-typed skill (or alias) to its canonical display name."""
    key = name.strip().lower()
    if not key:
        return None
    if key in SKILL_BY_NAME:
        return SKILL_BY_NAME[key].name
    for skill in SKILLS:
        if key in skill.aliases or name.strip() in skill.case_sensitive:
            return skill.name
    return None


_WORD = r"[a-z0-9]"


def _alias_pattern(alias: str) -> str:
    # Custom boundaries so "c++", "c#", ".net", "node.js" match cleanly while
    # "java" does not match inside "javascript".
    lead = "" if alias.startswith(".") else r"(?<!\.)"  # "js" must not match inside "node.js"
    return rf"(?<!{_WORD})(?<![+#]){lead}{re.escape(alias)}(?!{_WORD})(?![+#])"


@lru_cache(maxsize=1)
def _compiled() -> list[tuple[Skill, re.Pattern[str] | None, re.Pattern[str] | None]]:
    out = []
    for skill in SKILLS:
        ci = re.compile("|".join(_alias_pattern(a) for a in skill.aliases)) if skill.aliases else None
        cs = None
        if skill.case_sensitive:
            # Bare "C" / "R" / "Go": must stand alone, and not be an initial ("John C. Smith")
            # or part of a grade/label like "C-" or "R&D".
            parts = [rf"(?<![\w+#./&-]){re.escape(a)}(?![\w+#&-])(?!\.\w)(?!\.\s+[A-Z][a-z])"
                     for a in skill.case_sensitive]
            cs = re.compile("|".join(parts))
        out.append((skill, ci, cs))
    return out


def extract_skills(text: str) -> dict[str, int]:
    """Return {canonical skill name: mention count} found in ``text``."""
    if not text:
        return {}
    lower = text.lower()
    found: dict[str, int] = {}
    for skill, ci, cs in _compiled():
        n = 0
        if ci is not None:
            n += len(ci.findall(lower))
        if cs is not None:
            n += len(cs.findall(text))
        if n:
            found[skill.name] = n
    return found


def skill_group(name: str) -> str:
    skill = SKILL_BY_NAME.get(name.lower())
    return skill.group if skill else "other"


# ---------------------------------------------------------------------------
# Role signatures
# ---------------------------------------------------------------------------

CATEGORIES = ("Software", "AI/ML/Data", "Hardware", "Quant", "Product")

# Weighted skill profile for each broad internship category.
CATEGORY_SIGNATURES: dict[str, dict[str, float]] = {
    "Software": {
        "Python": 2, "Java": 2, "C++": 2, "JavaScript": 2, "TypeScript": 2, "Go": 1.5, "C#": 1,
        "React": 1.5, "Node.js": 1.5, "SQL": 1, "Git": 2, "Linux": 1, "AWS": 1, "Docker": 1,
        "REST APIs": 1, "Algorithms": 2, "Distributed Systems": 1, "Kotlin": 0.5, "Swift": 0.5,
        "Rust": 0.5, "Django": 0.5, "Flask": 0.5, "Spring": 0.5, "Kubernetes": 0.5,
        "PostgreSQL": 0.5, "HTML": 0.5, "CSS": 0.5, "CI/CD": 0.5, "Testing": 1,
        "Object-Oriented Programming": 1,
    },
    "AI/ML/Data": {
        "Python": 3, "Machine Learning": 3, "PyTorch": 2, "TensorFlow": 1.5, "scikit-learn": 2,
        "Pandas": 2, "NumPy": 2, "SQL": 2, "Statistics": 2, "Deep Learning": 2, "NLP": 1,
        "Computer Vision": 1, "LLMs": 1, "Data Analysis": 2, "Tableau": 1, "Spark": 1, "R": 1,
        "Matplotlib": 1, "Excel": 0.5, "A/B Testing": 1, "Airflow": 0.5, "Probability": 1,
    },
    "Hardware": {
        "Verilog": 3, "SystemVerilog": 2, "VHDL": 2, "FPGA": 2, "PCB Design": 2, "Circuit Design": 2,
        "Embedded Systems": 2, "C": 2, "MATLAB": 2, "Signal Processing": 1, "Oscilloscope": 1,
        "Computer Architecture": 2, "ASIC": 1, "Firmware": 1, "Python": 1, "CAD": 1, "RF": 1,
        "Power Electronics": 1, "Controls": 1, "Digital Design": 2, "C++": 0.5,
    },
    "Quant": {
        "Python": 2, "C++": 3, "Probability": 3, "Statistics": 3, "Linear Algebra": 1,
        "Stochastic Calculus": 1, "Finance": 1, "Algorithms": 2, "Competitive Programming": 2,
        "Time Series": 1, "NumPy": 1, "Pandas": 1, "Optimization": 1, "Machine Learning": 1, "R": 0.5,
    },
    "Product": {
        "Product Management": 3, "User Research": 2, "Figma": 2, "UX/UI Design": 2,
        "Product Analytics": 2, "SQL": 2, "Agile": 1, "Excel": 1, "A/B Testing": 1, "Tableau": 1,
        "Market Research": 1, "Data Analysis": 1,
    },
}


def normalize_category(raw: str | None) -> str:
    """Map the various category spellings in listing feeds to one of CATEGORIES (or "Other")."""
    if not raw:
        return "Other"
    r = raw.strip().lower()
    if r.startswith("software") or r in {"swe", "engineering"}:
        return "Software"
    if "data" in r or "machine learning" in r or r.startswith("ai") or "/ml" in r:
        return "AI/ML/Data"
    if r.startswith("hardware") or "electrical" in r:
        return "Hardware"
    if r.startswith("quant"):
        return "Quant"
    if r.startswith("product"):
        return "Product"
    return "Other"


# Title keyword rules. Each rule adds "requirement groups": a group is satisfied
# if the candidate has ANY skill in it. Rules are checked in order; several can match.
TITLE_RULES: tuple[tuple[str, str, tuple[tuple[str, ...], ...]], ...] = (
    (r"full[\s-]?stack", "Full-stack",
     (("React", "Vue", "Angular", "Svelte", "Next.js"), ("JavaScript", "TypeScript"),
      ("Node.js", "Django", "Flask", "Spring", "Express", "FastAPI", "Rails", ".NET"),
      ("SQL", "PostgreSQL", "MySQL", "MongoDB"))),
    (r"front[\s-]?end|\bweb\b|\bui engineer", "Frontend",
     (("React", "Vue", "Angular", "Svelte", "Next.js"), ("JavaScript", "TypeScript"), ("HTML", "CSS"))),
    (r"back[\s-]?end|server[\s-]side|\bapi\b|\bplatform\b", "Backend",
     (("Java", "Go", "Python", "C++", "C#", "Node.js", "Rust", "Kotlin", "Scala", "Ruby"),
      ("SQL", "PostgreSQL", "MySQL", "MongoDB", "Redis", "DynamoDB"),
      ("REST APIs", "GraphQL", "gRPC", "Microservices", "Distributed Systems"))),
    (r"\bios\b", "iOS", (("Swift", "Objective-C", "SwiftUI"), ("iOS",))),
    (r"android", "Android", (("Kotlin", "Java"), ("Android",))),
    (r"mobile", "Mobile", (("Swift", "Kotlin", "React Native", "Flutter", "iOS", "Android"),)),
    (r"machine learning|\bml\b|\bai\b|artificial intelligence|deep learning|\bmle\b", "Machine learning",
     (("Machine Learning", "Deep Learning"), ("PyTorch", "TensorFlow", "scikit-learn", "Keras", "XGBoost"),
      ("Python",))),
    (r"data scien", "Data science",
     (("Python", "R"), ("Statistics", "Machine Learning"), ("SQL",), ("Pandas", "NumPy", "scikit-learn"))),
    (r"data engineer", "Data engineering",
     (("Python", "Scala", "Java"), ("SQL",), ("Spark", "Airflow", "Kafka", "dbt", "Hadoop", "Snowflake",
                                              "BigQuery", "ETL"))),
    (r"analyst|analytics|business intelligence|\bbi\b", "Analytics",
     (("SQL",), ("Excel", "Tableau", "Power BI"), ("Python", "R"), ("Statistics", "Data Analysis"))),
    (r"computer vision|perception|\bvision\b", "Computer vision",
     (("Computer Vision",), ("PyTorch", "TensorFlow"), ("Python", "C++"))),
    (r"\bnlp\b|natural language|\bllm|gen(erative)?[\s-]?ai|language model", "NLP / LLMs",
     (("NLP", "LLMs"), ("PyTorch", "TensorFlow"), ("Python",))),
    (r"embedded|firmware|microcontroller", "Embedded",
     (("C", "C++"), ("Embedded Systems", "Firmware"), ("Oscilloscope", "PCB Design", "Linux", "Computer Architecture"))),
    (r"fpga|asic|\brtl\b|digital design|design verification|\bdv\b|verification|silicon|soc\b", "Digital design",
     (("Verilog", "SystemVerilog", "VHDL"), ("FPGA", "ASIC", "Digital Design"), ("Computer Architecture", "Python"))),
    (r"analog|mixed[\s-]signal|circuit|electrical|power electronics", "Circuits",
     (("Circuit Design", "Power Electronics"), ("MATLAB", "Python"), ("PCB Design", "Oscilloscope"))),
    (r"\bpcb\b|hardware engineer|hardware design|board design", "Board-level hardware",
     (("PCB Design",), ("Circuit Design",), ("Embedded Systems", "Oscilloscope"))),
    (r"\brf\b|antenna|wireless", "RF", (("RF",), ("Signal Processing",), ("MATLAB", "Python"))),
    (r"signal processing|\bdsp\b", "Signal processing", (("Signal Processing",), ("MATLAB", "Python", "C++"))),
    (r"robot", "Robotics", (("Robotics", "ROS"), ("C++", "Python"), ("Controls", "Computer Vision"))),
    (r"mechanical|mechatronic", "Mechanical", (("CAD",), ("MATLAB", "Controls"))),
    (r"devops|\bsre\b|site reliability|infrastructure|\bcloud\b|production engineer", "Infrastructure",
     (("AWS", "GCP", "Azure"), ("Docker", "Kubernetes"), ("Linux",), ("Terraform", "CI/CD", "Bash"))),
    (r"security|cyber|offensive|red team", "Security",
     (("Cybersecurity", "Penetration Testing", "Cryptography", "Reverse Engineering"), ("Linux", "Networking"),
      ("Python", "C", "C++"))),
    (r"quant(itative)?\s+(research|researcher|analyst)|\bresearch\b.*quant|alpha", "Quant research",
     (("Probability", "Statistics"), ("Python", "R", "C++"), ("Machine Learning", "Time Series", "Linear Algebra"))),
    (r"quant(itative)?\s+(dev|developer|engineer|software)|trading system|low[\s-]latency|\bhft\b", "Quant dev",
     (("C++",), ("Algorithms",), ("Linux",), ("Python",))),
    (r"trad(er|ing)\b", "Trading",
     (("Probability", "Statistics"), ("Python", "Excel"), ("Finance", "Competitive Programming"))),
    (r"product manag|\bapm\b|\bpm\b|product intern|product owner", "Product management",
     (("Product Management",), ("User Research", "UX/UI Design"), ("Product Analytics", "SQL", "Data Analysis"),
      ("Agile",))),
    (r"designer|\bux\b|\bui\b|user experience|product design", "Design",
     (("Figma",), ("UX/UI Design", "User Research"))),
    (r"game", "Games", (("Game Development",), ("C++", "C#"))),
    (r"compiler", "Compilers", (("C++", "Rust"), ("Compilers",))),
    (r"operating system|kernel|systems (software|engineer)|\bsystems\b", "Systems",
     (("C", "C++", "Rust"), ("Linux",), ("Operating Systems", "Computer Architecture", "Distributed Systems"))),
    (r"\bgpu\b|cuda|high[\s-]performance|\bhpc\b|performance engineer", "GPU / HPC",
     (("CUDA",), ("C++",), ("Computer Architecture", "Linux"))),
    (r"test engineer|\bqa\b|quality assurance|\bsdet\b|automation", "Test / QA",
     (("Testing",), ("Python", "Java", "JavaScript", "C++"))),
    (r"software|\bswe\b|developer|programm|engineer", "General software",
     (("Python", "Java", "C++", "JavaScript", "TypeScript", "Go", "C#", "Rust", "Kotlin", "Swift", "C"),
      ("Git",), ("Algorithms", "Object-Oriented Programming", "Testing"))),
)

_TITLE_RULES_COMPILED = tuple((re.compile(p, re.I), label, groups) for p, label, groups in TITLE_RULES)


def title_requirements(title: str) -> list[tuple[str, tuple[tuple[str, ...], ...]]]:
    """Return the (label, requirement groups) rules that apply to a job title.

    The generic "software engineer" rule is only used when nothing more specific matched.
    """
    matched = [(label, groups) for rx, label, groups in _TITLE_RULES_COMPILED if rx.search(title)]
    specific = [m for m in matched if m[0] != "General software"]
    return specific or matched
