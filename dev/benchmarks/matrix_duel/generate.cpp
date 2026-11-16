#include <vector>
#include <cstdint>
#include <fstream>
#include <filesystem>
#include <iostream>
#include <string>
template<class T>void save(const std::string&path,const std::vector<T>&v){std::ofstream f(path,std::ios::binary);f.write((const char*)v.data(),v.size()*sizeof(T));if(!f)throw std::runtime_error(path);}
int main(int argc,char**argv){
 if(argc!=4)return 2;
 int side=std::stoi(argv[2]);bool spd=std::string(argv[3])=="spd";int n=side*side*side;
 std::vector<uint64_t> cp(n+1);std::vector<int32_t> rows;std::vector<double> values;
 rows.reserve(n*7);values.reserve(n*7);
 for(int column=0;column<n;column++){
  cp[column]=rows.size();int x=column%side,y=(column/side)%side,z=column/(side*side);
  auto put=[&](int row,double value){rows.push_back(row);values.push_back(value);};
  if(z)put(column-side*side,spd?-1.:-1.05);
  if(y)put(column-side,spd?-1.:-1.1);
  if(x)put(column-1,spd?-1.:-1.25);
  put(column,spd?7.:10.);
  if(x+1<side)put(column+1,spd?-1.:-.75);
  if(y+1<side)put(column+side,spd?-1.:-.9);
  if(z+1<side)put(column+side*side,spd?-1.:-.95);
 }
 cp[n]=rows.size();std::filesystem::create_directories(argv[1]);std::string root=argv[1];
 save(root+"/ptr.u64",cp);save(root+"/idx.i32",rows);save(root+"/values.bin",values);
 std::cout<<"{\"n\":"<<n<<",\"nnz\":"<<rows.size()<<",\"side\":"<<side<<",\"dtype\":\"real\",\"type\":\""<<(spd?"spd":"general")<<"\",\"layout\":\"CSC\"}"<<std::endl;
}
