cmake_minimum_required(VERSION 3.16)
project(embedded_bridge VERSION 0.1.0 LANGUAGES CXX)

add_library(embedded_bridge INTERFACE)
target_include_directories(embedded_bridge INTERFACE
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>
)
target_compile_features(embedded_bridge INTERFACE cxx_std_17)

if(CMAKE_PROJECT_NAME STREQUAL PROJECT_NAME)
    include(CTest)
    if(BUILD_TESTING)
        add_subdirectory(test)
    endif()
endif()
