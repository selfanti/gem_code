from ulid import ULID
# 从当前时间戳创建ULID
ulid = ULID()
print(ulid.to_uuid()) # 输出类似于ULID(01E75HZVW36EAZKMF1W7XNMSB4)
# 使用命名构造函数
import time, datetime
ulid_from_timestamp = ULID.from_timestamp(time.time())
print(ulid_from_timestamp) # 输出类似于ULID(01E75J1MKKWMGG0N5MBHFMRC84)
ulid_from_datetime = ULID.from_datetime(datetime.datetime.now())
print(ulid_from_datetime) # 输出类似于ULID(01E75J2XBK390V2XRH44EHC10X)