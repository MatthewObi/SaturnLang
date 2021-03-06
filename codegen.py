from llvmlite import ir, binding

char = ir.IntType(8)
int8ptr = char.as_pointer()
int32 = ir.IntType(32)
int64 = ir.IntType(64)
double = ir.DoubleType()
void = ir.VoidType()

compile_target = 'windows-x64'

class CodeGen():
    def __init__(self, filename, opt_level):
        self.binding = binding
        self.filename = filename
        self.opt_level = opt_level
        self.binding.initialize()
        self.binding.initialize_native_target()
        self.binding.initialize_native_asmprinter()
        self._config_llvm()
        self._create_execution_engine()
        self._declare_print_function()

    def _config_llvm(self):
        # Config LLVM
        self.module = ir.Module(name=self.filename)
        if compile_target == 'wasm':
            self.module.triple = "wasm32-unknown-wasi"
        elif compile_target == 'windows-x64':
            self.module.triple = "x86_64-pc-windows-msvc"
        elif compile_target == 'linux-x64':
            self.module.triple = "x86_64-pc-linux-gcc"
        self.module.di_file = self.module.add_debug_info("DIFile", {
            "filename": "main.sat",
            "directory": "saturn",
        })
        self.module.di_compile_unit = self.module.add_debug_info("DICompileUnit", {
            "language": ir.DIToken("DW_LANG_C99"),
            "file": self.module.di_file,
            "producer": "llvmlite x.y",
            "runtimeVersion": 2,
            "isOptimized": True,
            "flags": "-O2"
        }, is_distinct=True)

        self.module.di_types = {}
        self.module.di_types["int"] = self.module.add_debug_info("DIBasicType", {
            "name": "int",
            "size": 32,
            "encoding": ir.DIToken("DW_ATE_signed")
        })
        self.module.di_types["float32"] = self.module.add_debug_info("DIBasicType", {
            "name": "float32",
            "size": 32,
            "encoding": ir.DIToken("DW_ATE_float")
        })
        self.module.di_types["float64"] = self.module.add_debug_info("DIBasicType", {
            "name": "float64",
            "size": 64,
            "encoding": ir.DIToken("DW_ATE_float")
        })
        self.module.di_types["cstring"] = self.module.add_debug_info("DIBasicType", {
            "name": "cstring",
            "size": 64,
            "encoding": ir.DIToken("DW_ATE_address")
        })
        self.module.di_types["bool"] = self.module.add_debug_info("DIBasicType", {
            "name": "bool",
            "size": 8,
            "encoding": ir.DIToken("DW_ATE_boolean")
        })
        self.module.sfunctys = {}
        self.module.sglobals = {}
        self.module.add_named_metadata("llvm.dbg.cu", self.module.di_compile_unit)
        self.module.add_named_metadata("llvm.ident", ["llvmlite/1.0"])
        self.module.add_named_metadata("llvm.module.flags", [
            int32(2), 'Dwarf Version', int32(2)
        ])
        self.module.add_named_metadata("llvm.module.flags", [
            int32(2), 'Debug Info Version', int32(3)
        ])
        self.module.add_named_metadata("llvm.module.flags", [
            int32(1), 'PIC Level', int32(2)
        ])
        self.module.memset = self.module.declare_intrinsic('llvm.memset', [int8ptr, int32])
        self.builder = ir.IRBuilder()

    def _create_execution_engine(self):
        """
        Create an ExecutionEngine suitable for JIT code generation on
        the host CPU.  The engine is reusable for an arbitrary number of
        modules.
        """
        target = self.binding.Target.from_default_triple()
        target_machine = target.create_target_machine()
        self.target_machine = target_machine
        # And an execution engine with an empty backing module
        backing_mod = binding.parse_assembly("")
        engine = binding.create_mcjit_compiler(backing_mod, target_machine)
        self.engine = engine

    def _declare_print_function(self):
        # Declare Printf function
        voidptr_ty = ir.IntType(8).as_pointer()
        printf_ty = ir.FunctionType(ir.IntType(32), [voidptr_ty], var_arg=True)
        printf = ir.Function(self.module, printf_ty, name="printf")
        self.printf = printf

        llvm_dbg_ty = ir.FunctionType(void, [ir.MetaDataType(), ir.MetaDataType(), ir.MetaDataType()])
        llvm_dbg_val = ir.Function(self.module, llvm_dbg_ty, "llvm.dbg.value")
        self.module.llvm_dbg_value = llvm_dbg_val

        llvm_dbg_decl_ty = ir.FunctionType(void, [ir.MetaDataType(), ir.MetaDataType(), ir.MetaDataType()])
        llvm_dbg_decl = ir.Function(self.module, llvm_dbg_decl_ty, "llvm.dbg.addr")
        self.module.llvm_dbg_decl = llvm_dbg_decl

        # Declare strlen
        # strlen_ty = ir.FunctionType(ir.IntType(32), [voidptr_ty])
        # strlen = ir.Function(self.module, strlen_ty, name="strlen")
        # self.strlen = strlen

    def _compile_ir(self):
        """
        Compile the LLVM IR string with the given engine.
        The compiled module object is returned.
        """
        # Create a LLVM module object from the IR
        llvm_ir = str(self.module)
        mod = self.binding.parse_assembly(llvm_ir.replace('target triple = ', 'source_filename = "%s"\ntarget triple = ' % self.filename))
        mod.name = self.module.name
        mod.verify()
        if self.opt_level > 0:
            # Opt module
            pmb = self.binding.PassManagerBuilder()
            pmb.opt_level = self.opt_level
            if pmb.opt_level < 2:
                pmb.disable_unroll_loops = True
            if pmb.opt_level > 1:
                pmb.inlining_threshold = 2
            mpm = self.binding.ModulePassManager()
            pmb.populate(mpm)
            if mpm.run(mod):
                print('opt ', self.module.name)

            # Opt functions
            fpm = self.binding.FunctionPassManager(mod)
            pmb.populate(fpm)
            fpm.initialize()
            for f in mod.functions:
                if not fpm.run(f):
                    pass #print('noopt ', f.name)
                else:
                    pass #print('opt ', f.name)
            fpm.finalize()

        # Now add the module and make sure it is ready for execution
        self.engine.add_module(mod)
        self.engine.finalize_object()
        self.engine.run_static_constructors()
        return mod

    def create_ir(self):
        return self._compile_ir()

    def save_ir(self, filename, ir):
        with open(filename, 'w') as output_file:
            output_file.write(str(ir))

    def save_obj(self, filename, ir):
        obj = self.target_machine.emit_object(ir)
        with open(filename, 'wb') as output_file:
            output_file.write(obj)