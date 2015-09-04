/*
 * Licensed to Cloudera, Inc. under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  Cloudera, Inc. licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package com.cloudera.hue.livy.repl

import java.util.concurrent.Executors

import com.cloudera.hue.livy.{Utils, Logging}
import com.cloudera.hue.livy.sessions._
import org.json4s.JsonDSL._
import org.json4s.{JValue, DefaultFormats, Extraction}

import _root_.scala.concurrent.duration.Duration
import _root_.scala.concurrent.{TimeoutException, ExecutionContext, Future}

object Session {
  val STATUS = "status"
  val OK = "ok"
  val ERROR = "error"
  val EXECUTION_COUNT = "execution_count"
  val DATA = "data"
  val ENAME = "ename"
  val EVALUE = "evalue"
  val TRACEBACK = "traceback"

  def apply(interpreter: Interpreter): Session = new Session(interpreter)
}

class Session(interpreter: Interpreter)
  extends Logging
{
  import Session._

  private implicit val executor = ExecutionContext.fromExecutorService(Executors.newSingleThreadExecutor())
  private implicit val formats = DefaultFormats

  private var _state: State = NotStarted()
  private var _history = IndexedSeq[Statement]()

  Future {
    _state = Starting()
    interpreter.start()
    _state = Idle()
  }.onFailure { case _ =>
    _state = Error()
  }

  def kind: String = interpreter.kind

  def state = _state

  def history: IndexedSeq[Statement] = _history

  def execute(code: String): Statement = synchronized {
    val executionCount = _history.length
    val statement = Statement(executionCount, Future { executeCode(executionCount, code) })
    _history :+= statement
    statement
  }

  def close(): Unit = {
    executor.shutdown()
    interpreter.close()
  }

  def clearHistory() = synchronized {
    _history = IndexedSeq()
  }

  @throws(classOf[TimeoutException])
  @throws(classOf[InterruptedException])
  def waitForStateChange(oldState: State, atMost: Duration) = {
    Utils.waitUntil({ () => state != oldState }, atMost)
  }

  private def executeCode(executionCount: Int, code: String) = {
    _state = Busy()

    try {

      interpreter.execute(code) match {
        case Interpreter.ExecuteSuccess(data) =>
          _state = Idle()

          (STATUS -> OK) ~
          (EXECUTION_COUNT -> executionCount) ~
          (DATA -> data)
        case Interpreter.ExecuteIncomplete() =>
          _state = Idle()

          (STATUS -> ERROR) ~
          (EXECUTION_COUNT -> executionCount) ~
          (ENAME -> "Error") ~
          (EVALUE -> "incomplete statement") ~
          (TRACEBACK -> List())
        case Interpreter.ExecuteError(ename, evalue, traceback) =>
          _state = Idle()

          (STATUS -> ERROR) ~
          (EXECUTION_COUNT -> executionCount) ~
          (ENAME -> ename) ~
          (EVALUE -> evalue) ~
          (TRACEBACK -> traceback)
        case Interpreter.ExecuteAborted(message) =>
          _state = Error()

          (STATUS -> ERROR) ~
          (EXECUTION_COUNT -> executionCount) ~
          (ENAME -> "Error") ~
          (EVALUE -> f"Interpreter died:\n$message") ~
          (TRACEBACK -> List())
      }
    } catch {
      case e: Throwable =>
        error("Exception when executing code", e)

        _state = Idle()


        (STATUS -> ERROR) ~
        (EXECUTION_COUNT -> executionCount) ~
        (ENAME -> f"Internal Error: ${e.getClass.getName}") ~
        (EVALUE -> e.getMessage) ~
        (TRACEBACK -> List())
    }
  }
}

case class Statement(id: Int, result: Future[JValue])
