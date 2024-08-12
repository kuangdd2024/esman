"""
## 问答搜索系统
主要包括知识库管理和搜索问答服务。
"""
import base64
import hashlib
import json
import re
import os

import simhash
from elasticsearch import Elasticsearch
import tqdm

# from fastapi import FastAPI
#
# app = FastAPI()

from fastapi_fixer import app

_embedding_dims = 32  # 确保和向量模型的维数相同，这里用simhash，维数为32

_es_url = os.environ.get('ES_URL', 'http://127.0.0.1:9200')
_username = os.environ.get('ES_AUTH_USERNAME', 'elastic')
_password = os.environ.get('ES_AUTH_PASSWORD', '')
client = Elasticsearch(_es_url, basic_auth=(_username, _password))

# punc_re = re.compile(r'([^。？！；，?!;,]*[。？！；，?!;,]+)')
punc_re = re.compile(r'(\w*\W+)')


@app.post("/manage")
def manage(_index='tmp_index', _id='tmp_id', content='你好！这是智能搜索引擎。', _source='{}'):
    """知识库管理主接口。
    dict(_index='tmp_index', _id='tmp_id', _source=dict(content='你好！这是智能搜索引擎。'))"""
    _source = json.loads(_source)
    _source['content'] = content
    data = dict(_index=_index, _id=_id, _source=_source)
    kdata_lst, fdata_lst = convert_data(data)

    kflag = read_kindex(kdata_lst[0]['_index'])
    if not kflag['exists']:
        create_kindex(kdata_lst[0]['_index'])
        client.indices.refresh(index=kdata_lst[0]['_index'])

    fflag = read_findex(fdata_lst[0]['_index'])
    if not fflag['exists']:
        create_findex(fdata_lst[0]['_index'])
        client.indices.refresh(index=fdata_lst[0]['_index'])

    k_flags = []
    for kdata in kdata_lst:
        kdata, kop = assign_kop(kdata)
        kflag = kop(**kdata)
        client.indices.refresh(index=kdata['_index'])
        k_flags.append(kflag.body if kflag else kflag)

    f_flags = []
    for fdata in fdata_lst:
        fdata, fop = assign_fop(fdata)
        fflag = fop(**fdata)
        client.indices.refresh(index=fdata['_index'])
        f_flags.append(fflag.body if fflag else fflag)
    return dict(k_flags=k_flags, f_flags=f_flags)


@app.post("/search")
def search(_index='tmp_index', query='你好', topn=3, threshold=0.5):
    """搜索问答服务主接口。
    dict(_index='tmp_index', query='你好')"""
    feas = convert_knowledge_to_feature(query)

    f_result = []
    for fea in feas:
        vec = convert_text_to_vector(fea)
        f_res = search_feature('f_' + _index, dict(vector=vec))
        f_result.extend(f_res)

    k_result = search_knowledge('k_' + _index, f_result)
    fydt = {f_dt['_source']['text']: f_dt['_score'] for f_dt in f_result}
    for k_dt in k_result:
        y_lst = []
        for text in k_dt['_source']['content']:
            if text in fydt:
                y_lst.append(fydt[text])
        k_dt['_score'] = max(y_lst) if y_lst else 0
    if k_result:
        k_result = [dt for dt in sorted(k_result, key=lambda x: x['_score'], reverse=True)[:int(topn)]
                    if dt['_score'] >= float(threshold)]

    k_result_resort = resort(query, k_result)
    return k_result_resort


@app.post("/rebuild_findex")
def rebuild_findex(_index='tmp_index'):
    """重建特征库，根据知识库重建特征库。
    ```
    方案1：根据特征库的数据，重新生成特征数据。
    方案2：根据知识库的数据，重新生成特征数据。
    方案3：根据数据库的数据，重新生成知识数据和特征数据。

    采用方案2，流程：
    1. 读取特征库的所有文本。
    2. 特征备份到本地。
    3. 删除特征库。
    4. 创建新的特征库。
    5. 根据原特征库文本创建特征。
    ```
    """
    f_index = f'f_{_index}'
    k_index = f'k_{_index}'

    kflag = read_kindex(k_index)
    if not kflag['exists']:
        create_kindex(k_index)
        client.indices.refresh(index=k_index)

    fflag = read_findex(f_index)
    if not fflag['exists']:
        create_findex(f_index)
        client.indices.refresh(index=f_index)

    body = {
        "query": {
            "match_all": {}
        },
        "_source": ["text"]
    }
    feas = client.search(index=f_index, **body)

    bak_path = f'./tmp/findex_{f_index}_backup.json'
    os.makedirs(os.path.dirname(bak_path), exist_ok=True)
    json.dump(feas.body, open(bak_path, 'wt', encoding='utf8'), ensure_ascii=False, indent=4)

    body = {
        "query": {
            "match_all": {}
        },
        "_source": ["content"]
    }
    knows = client.search(index=k_index, **body)

    delete_findex(f_index)

    create_findex(f_index)
    client.indices.refresh(index=f_index)

    # flag = {}
    # for dt in tqdm.tqdm(feas["hits"]["hits"], 'rebuild_findex'):  # _index, _id, _source: {text}
    #     dt["_source"]["vector"] = convert_text_to_vector(dt["_source"]["text"])
    #     flag = create_feature(**dt)

    flag = set()
    for dt in tqdm.tqdm(knows["hits"]["hits"], 'rebuild_findex'):  # _index, _id, _source: {text}
        for text in dt["_source"]["content"]:
            if text in flag:
                continue
            flag.add(text)
            _id = convert_text_to_id(text)
            vector = convert_text_to_vector(text)
            _source = {"text": text, "vector": vector}
            create_feature(_index=f_index, _id=_id, _source=_source)
    client.indices.refresh(index=f_index)

    read_flag = read_findex(f_index)

    return dict(read_findex_flag=read_flag, create_feature_flag={"total": len(flag)})


@app.post("/rebuild_findex_total")
def rebuild_findex_total(_index_re='k_*'):
    """重建所有特征库。
    :return:
    """
    _k_index_dt = client.indices.get(index=_index_re)
    flags = []
    for k_index, meta in _k_index_dt.items():
        _index = k_index[2:]
        print(f'rebuilding findex: {_index} ...')
        rebuild_findex(_index=_index)
        flags.append(_index)
    return dict(rebuild_findex_flags=flags)


@app.post("/match_all")
def match_all(_index='f_tmp_index', _source='["text"]'):
    body = {
        "query": {
            "match_all": {}
        },
        "_source": json.loads(_source)
    }
    feas = client.search(index=_index, **body)
    return feas.body


def get_op(_op):
    """获取知识库操作动作映射函数。"""
    op_dict = dict(
        create_knowledge=create_knowledge,
        delete_knowledge=delete_knowledge,
        update_knowledge=update_knowledge,
        read_knowledge=read_knowledge,
        create_feature=create_feature,
        delete_feature=delete_feature,
        update_feature=update_feature,
        read_feature=read_feature,
        no_action=no_action
    )
    return op_dict.get(_op)


@app.post("/create_kindex")
def create_kindex(_index):
    """创建知识索引。"""
    mappings = {
        "properties": {
            "content": {
                "type": "keyword"
            },
            "answer": {
                "type": "text"
            }
        }
    }
    return client.indices.create(index=_index, mappings=mappings)


@app.post("/delete_kindex")
def delete_kindex(_index):
    """删除知识索引。"""
    return client.indices.delete(index=_index)


@app.post("/read_kindex")
def read_kindex(_index):
    """查询知识索引。"""
    flag = client.indices.exists(index=_index)
    if flag:
        mapping = client.indices.get_mapping(index=_index)
        settings = client.indices.get_settings(index=_index)
        count = client.count(index=_index)
        result = dict(exists=flag.body, count=count, settings=settings.body, mapping=mapping.body)
    else:
        result = dict(exists=flag.body, count=0, settings={}, mapping={})
    return result


@app.post("/create_knowledge")
def create_knowledge(_index, _id, _source, **kwargs):
    """创建知识。"""
    return client.index(index=_index, id=_id, document=_source)


@app.post("/delete_knowledge")
def delete_knowledge(_index, _id, **kwargs):
    """删除知识。"""
    return client.delete(index=_index, id=_id)


@app.post("/update_knowledge")
def update_knowledge(_index, _id, _source, **kwargs):
    """更新知识。"""
    return client.update(index=_index, id=_id, doc=_source)


@app.post("/read_knowledge")
def read_knowledge(_index, _id, **kwargs):
    """查询知识。"""
    try:
        result = client.get(index=_index, id=_id)
        return result['_source']
    except Exception as e:
        print(e)
        return None


@app.post("/search_knowledge")
def search_knowledge(_index, hits):
    """根据特征搜索知识。"""
    body = {
        "query": {
            "bool": {
                "should": [
                    {
                        "term": {
                            "content": w["_source"]["text"]
                        }
                    }
                    for w in hits
                ]
            }
        }
    }
    result = client.search(index=_index, **body)
    return result["hits"]["hits"]


@app.post("/create_findex")
def create_findex(_index):
    """创建特征索引。"""
    mappings = {
        "properties": {
            "vector": {
                "type": "dense_vector",
                "dims": _embedding_dims,  # 768,
                "index": True,
                "similarity": "cosine"
            },
            "text": {
                "type": "keyword"
            }
        }
    }

    return client.indices.create(index=_index, mappings=mappings)


@app.post("/delete_findex")
def delete_findex(_index):
    """删除特征索引。"""
    return client.indices.delete(index=_index)


@app.post("/read_findex")
def read_findex(_index):
    """查询特征索引。"""
    flag = client.indices.exists(index=_index)
    if flag:
        mapping = client.indices.get_mapping(index=_index)
        settings = client.indices.get_settings(index=_index)
        result = dict(exists=flag.body, settings=settings.body, mapping=mapping.body)
    else:
        result = dict(exists=flag.body, settings={}, mapping={})
    return result


@app.post("/create_feature")
def create_feature(_index, _id, _source, **kwargs):
    """创建特征。"""
    return client.index(index=_index, id=_id, document=_source)


@app.post("/delete_feature")
def delete_feature(_index, _id, _source, **kwargs):
    """删除特征。"""
    k = check_knowledge(_index, _source)
    if not k:
        client.delete(index=_index, id=_id)


@app.post("/update_feature")
def update_feature(_index, _id, _source, **kwargs):
    """更新特征。"""
    return client.update(index=_index, id=_id, document=_source)


@app.post("/read_feature")
def read_feature(_index, _id, **kwargs):
    """查询特征。"""
    try:
        return client.get(index=_index, id=_id)
    except Exception as e:
        print(e)
        return None


def no_action(*args, **kwargs):
    """不做操作。"""
    return None


@app.post("/check_feature")
def check_feature(_index, _source):
    """查找特征库是否存在目标特征。"""
    body = {"query": {"term": {"text": _source["text"]}},
            "size": 1}
    result = client.search(index=_index, **body)
    return result["hits"]["hits"]


@app.post("/search_feature")
def search_feature(_index, _source):
    """根据向量搜索特征。"""
    body = {
        "knn": {
            "field": "vector",
            "query_vector": _source['vector'],
            "k": 10,
            "num_candidates": 100,
            "boost": 1
        },
        "_source": ["text"]
    }
    result = client.search(index=_index, **body)
    return result["hits"]["hits"]


@app.post("/check_knowledge")
def check_knowledge(_index, _source):
    """查找知识库是否存在目标特征。"""
    print(_source)
    body = {"query": {"term": {"content": _source["text"]}},
            "size": 1}
    result = client.search(index=_index, **body)
    return result["hits"]["hits"]


@app.post("/convert_text_to_id")
def convert_text_to_id(text):
    """文本转ID，文本和ID一一对应。"""
    res = base64.urlsafe_b64encode(hashlib.md5(text.encode("utf8")).digest()).decode('utf8')
    res = f'{res[-1]}{res[:-1]}'
    return res


@app.post("/convert_text_to_vector")
def convert_text_to_vector(text):
    """文本转向量。"""
    # hash
    # vec_hex = hashlib.md5(text.encode("utf8")).hexdigest()
    # vec = [eval(f'0x{w}') for w in vec_hex]

    # simhash
    vec_bin = bin(simhash.Simhash(text, _embedding_dims).value)
    vec_bin = vec_bin[2:].rjust(_embedding_dims, '0')
    vec = [int(w) for w in vec_bin]

    # todo 文本向量化，调用接口
    return vec


@app.post("/convert_knowledge_to_feature")
def convert_knowledge_to_feature(text):
    """文档转为句子列表。"""
    # todo 篇章分段，业务逻辑
    f = [text]
    # f = [w for w in re.split(punc_re, text) if w]
    return f


@app.post("/convert_data")
def convert_data(data):
    """数据转换成知识和特征。
    通常1条数据对应1条知识和N条特征。
    data: _index _id _source
    return: kdata_lst fdata_lst
    """
    feas = convert_knowledge_to_feature(data["_source"]["content"])  # 数据库的特征
    _source = {**data["_source"], **dict(content=feas)}
    kdata = {"_index": "k_" + data["_index"], "_id": data["_id"], "_source": _source,
             "_op": None}
    k_source = read_knowledge(kdata["_index"], kdata["_id"])  # 知识库的特征

    ks_qset = set(k_source["content"]) if k_source else set()
    kd_qset = set(feas)
    k_qd = ks_qset - kd_qset
    k_qc = kd_qset - ks_qset
    k_qk = kd_qset & ks_qset

    kdata_lst = [kdata]

    fdata_lst_c = [{"_index": "f_" + data["_index"],
                    "_id": convert_text_to_id(t),
                    "_source": {"text": t, "vector": convert_text_to_vector(t)},
                    "_op": "create_feature"}
                   for t in k_qc]
    fdata_lst_d = [{"_index": "f_" + data["_index"],
                    "_id": convert_text_to_id(t),
                    "_source": {"text": t, "vector": convert_text_to_vector(t)},
                    "_op": "delete_feature"}
                   for t in k_qd]
    fdata_lst_n = [{"_index": "f_" + data["_index"],
                    "_id": convert_text_to_id(t),
                    "_source": {"text": t, "vector": convert_text_to_vector(t)},
                    "_op": "no_action"}
                   for t in k_qk]
    fdata_lst = fdata_lst_c + fdata_lst_d + fdata_lst_n
    return kdata_lst, fdata_lst


@app.post("/assign_kop")
def assign_kop(data):
    """管理知识操作。
    data指明删则删，
    查询_id的知识，查无则增，查有则改
    """
    kop = get_op(data["_op"])
    if kop is None:
        k_kb = read_knowledge(_index=data["_index"], _id=data["_id"])
        if k_kb:
            k_flag = k_kb == data["_source"]
            if k_flag:
                kop = no_action
            else:
                kop = update_knowledge
        else:
            kop = create_knowledge
    return data, kop


@app.post("/assign_fop")
def assign_fop(data):
    """管理特征操作。
    同manage_kop"""
    kop = get_op(data["_op"])
    if kop is None:
        k_kb = read_feature(_index=data["_index"], _id=data["_id"])
        if k_kb:
            k_flag = k_kb["_source"]["text"] == data["_source"]["text"]
            if k_flag:
                kop = no_action
            else:
                kop = update_feature
        else:
            kop = create_feature
    return data, kop


@app.post("/result")
def resort(query, result):
    return result


if __name__ == "__main__":
    print(__file__)
    import uvicorn

    uvicorn.run(app=app, host='0.0.0.0', port=8080)
