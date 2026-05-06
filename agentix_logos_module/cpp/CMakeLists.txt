cmake_minimum_required(VERSION 3.14)
project(AgentixLogosModulePlugin LANGUAGES CXX)

include(GNUInstallDirs)

set(CMAKE_AUTOMOC ON)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ── Dependencies ──────────────────────────────────────────────

if(NOT DEFINED LOGOS_MODULE_ROOT)
    message(FATAL_ERROR "LOGOS_MODULE_ROOT not set. Pass -DLOGOS_MODULE_ROOT=<path>")
endif()

if(NOT DEFINED LOGOS_CPP_SDK_ROOT)
    message(FATAL_ERROR "LOGOS_CPP_SDK_ROOT not set. Pass -DLOGOS_CPP_SDK_ROOT=<path>")
endif()

# Detect layout: installed (include/) vs source (src/, cpp/)
set(_logos_module_is_source FALSE)
if(EXISTS "${LOGOS_MODULE_ROOT}/src/interface.h")
    set(_logos_module_is_source TRUE)
elseif(NOT EXISTS "${LOGOS_MODULE_ROOT}/include/module_lib/interface.h")
    message(FATAL_ERROR "logos-module not found at ${LOGOS_MODULE_ROOT}")
endif()

set(_cpp_sdk_is_source FALSE)
if(EXISTS "${LOGOS_CPP_SDK_ROOT}/cpp/logos_api.h")
    set(_cpp_sdk_is_source TRUE)
elseif(NOT EXISTS "${LOGOS_CPP_SDK_ROOT}/include/cpp/logos_api.h")
    message(FATAL_ERROR "logos-cpp-sdk not found at ${LOGOS_CPP_SDK_ROOT}")
endif()

# Qt
find_package(Qt6 REQUIRED COMPONENTS Core RemoteObjects)

# ── Code generator ────────────────────────────────────────────

set(METADATA_JSON "${CMAKE_CURRENT_SOURCE_DIR}/../metadata.json")
set(PLUGINS_OUTPUT_DIR "${CMAKE_BINARY_DIR}/modules")

if(_cpp_sdk_is_source)
    get_filename_component(LOGOS_DEPS_ROOT "${LOGOS_CPP_SDK_ROOT}" DIRECTORY)
    set(CPP_GENERATOR_BUILD_DIR "${LOGOS_DEPS_ROOT}/build/cpp-generator")
    set(CPP_GENERATOR "${CPP_GENERATOR_BUILD_DIR}/bin/logos-cpp-generator")

    if(NOT TARGET cpp_generator_build)
        add_custom_target(cpp_generator_build
            COMMAND bash "${LOGOS_CPP_SDK_ROOT}/cpp-generator/compile.sh"
            WORKING_DIRECTORY "${LOGOS_DEPS_ROOT}"
            COMMENT "Building logos-cpp-generator"
            VERBATIM
        )
    endif()

    add_custom_target(run_cpp_generator_agentix
        COMMAND "${CPP_GENERATOR}" --metadata "${METADATA_JSON}" --module-dir "${PLUGINS_OUTPUT_DIR}"
        WORKING_DIRECTORY "${LOGOS_DEPS_ROOT}"
        COMMENT "Running logos-cpp-generator on agentix_logos_module metadata"
        VERBATIM
    )
    add_dependencies(run_cpp_generator_agentix cpp_generator_build)
else()
    find_program(CPP_GENERATOR logos-cpp-generator
        PATHS ${LOGOS_CPP_SDK_ROOT}/bin NO_DEFAULT_PATH REQUIRED)

    add_custom_target(run_cpp_generator_agentix
        COMMAND "${CPP_GENERATOR}" --metadata "${METADATA_JSON}" --module-dir "${PLUGINS_OUTPUT_DIR}"
        WORKING_DIRECTORY "${CMAKE_BINARY_DIR}"
        COMMENT "Running logos-cpp-generator on agentix_logos_module metadata"
        VERBATIM
    )
endif()

# ── Plugin library ────────────────────────────────────────────

set(PLUGIN_SOURCES
    agentix_logos_plugin.cpp
    agentix_logos_plugin.h
    agentix_logos_interface.h
)

# Add logos-module interface
if(_logos_module_is_source)
    list(APPEND PLUGIN_SOURCES ${LOGOS_MODULE_ROOT}/src/interface.h)
else()
    list(APPEND PLUGIN_SOURCES ${LOGOS_MODULE_ROOT}/include/module_lib/interface.h)
endif()

# Add SDK sources (source layout only)
if(_cpp_sdk_is_source)
    list(APPEND PLUGIN_SOURCES
        ${LOGOS_CPP_SDK_ROOT}/cpp/logos_api.cpp
        ${LOGOS_CPP_SDK_ROOT}/cpp/logos_api.h
        ${LOGOS_CPP_SDK_ROOT}/cpp/logos_api_client.cpp
        ${LOGOS_CPP_SDK_ROOT}/cpp/logos_api_client.h
        ${LOGOS_CPP_SDK_ROOT}/cpp/logos_api_consumer.cpp
        ${LOGOS_CPP_SDK_ROOT}/cpp/logos_api_consumer.h
        ${LOGOS_CPP_SDK_ROOT}/cpp/logos_api_provider.cpp
        ${LOGOS_CPP_SDK_ROOT}/cpp/logos_api_provider.h
        ${LOGOS_CPP_SDK_ROOT}/cpp/token_manager.cpp
        ${LOGOS_CPP_SDK_ROOT}/cpp/token_manager.h
        ${LOGOS_CPP_SDK_ROOT}/cpp/module_proxy.cpp
        ${LOGOS_CPP_SDK_ROOT}/cpp/module_proxy.h
    )
endif()

add_library(agentix_logos_module_plugin SHARED ${PLUGIN_SOURCES})
set_target_properties(agentix_logos_module_plugin PROPERTIES PREFIX "")

add_dependencies(agentix_logos_module_plugin run_cpp_generator_agentix)

target_link_libraries(agentix_logos_module_plugin PRIVATE
    Qt6::Core
    Qt6::RemoteObjects
)

if(NOT _cpp_sdk_is_source)
    find_library(LOGOS_SDK_LIB logos_sdk
        PATHS ${LOGOS_CPP_SDK_ROOT}/lib NO_DEFAULT_PATH REQUIRED)
    target_link_libraries(agentix_logos_module_plugin PRIVATE ${LOGOS_SDK_LIB})
endif()

# Include dirs
target_include_directories(agentix_logos_module_plugin PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
)

if(_logos_module_is_source)
    target_include_directories(agentix_logos_module_plugin PRIVATE
        ${LOGOS_MODULE_ROOT}/src)
else()
    target_include_directories(agentix_logos_module_plugin PRIVATE
        ${LOGOS_MODULE_ROOT}/include/module_lib)
endif()

if(_cpp_sdk_is_source)
    target_include_directories(agentix_logos_module_plugin PRIVATE
        ${LOGOS_CPP_SDK_ROOT}/cpp
        ${LOGOS_CPP_SDK_ROOT}/cpp/generated)
else()
    target_include_directories(agentix_logos_module_plugin PRIVATE
        ${LOGOS_CPP_SDK_ROOT}/include
        ${LOGOS_CPP_SDK_ROOT}/include/cpp
        ${LOGOS_CPP_SDK_ROOT}/include/core)
endif()

# Output properties
set_target_properties(agentix_logos_module_plugin PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/modules"
    BUILD_WITH_INSTALL_RPATH TRUE
    SKIP_BUILD_RPATH FALSE
    INSTALL_RPATH "$ORIGIN"
)

# ── Install ───────────────────────────────────────────────────

install(TARGETS agentix_logos_module_plugin
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}/logos/modules
)

install(FILES ${CMAKE_CURRENT_SOURCE_DIR}/../metadata.json
    DESTINATION ${CMAKE_INSTALL_DATADIR}/agentix-logos-module
)
