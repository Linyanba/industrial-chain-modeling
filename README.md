本项目使用Docker运行Qdrant  
使用Ollama来部署本地大模型，本地大模型使用的是qwen3:8b  
Embedding模型：Qwen/Qwen3-Embedding-0.6B  
一键运行指令：  
.\run_chain.ps1 -Pdf "中国半导体白皮书.pdf","半导体白皮书B.pdf" -RootLabel "半导体产业链" -RootModel "半导体产业链"  
RootLabel:产业链根节点名字  
RootModel：影响产业链知识库分割，相同Model在同一区域  
知识库功能：不同产业之间独立分割，互不影响。同一产业中可以有多个pdf，相同pdf不会重复处理（即不会重复进入知识库）
