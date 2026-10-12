print("A", flush=True)

import jieba

print("B", flush=True)

import jieba.analyse

print("C", flush=True)

print(jieba.lcut("今天推荐粉底液"), flush=True)

print("D", flush=True)

print(jieba.analyse.extract_tags("今天推荐粉底液"), flush=True)

print("E", flush=True)
