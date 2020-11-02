package hdlp.vhdl;

import java.util.ArrayList;
import java.util.List;

import org.antlr.v4.runtime.BaseErrorListener;
import org.antlr.v4.runtime.RecognitionException;
import org.antlr.v4.runtime.Recognizer;
import org.antlr.v4.runtime.misc.Utils;

public class vhdlSyntaxErrorListener extends BaseErrorListener {
    private final List<vhdlSyntaxError> syntaxErrors = new ArrayList<>();
    
    public vhdlSyntaxErrorListener() {}

    public List<vhdlSyntaxError> getSyntaxErrors() {
      return syntaxErrors;
    }

    @Override
    public void syntaxError(
        Recognizer<?, ?> recognizer,
        Object offendingSymbol,
        int line,
        int charPositionInLine,
        String msg,
        RecognitionException e) {
        syntaxErrors.add(new vhdlSyntaxError(recognizer, offendingSymbol, line, charPositionInLine, msg, e));
    }

    @Override
    public String toString() {
        return Utils.join(syntaxErrors.iterator(), "\n");
    }
}
